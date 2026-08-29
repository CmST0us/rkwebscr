#include <pipewire/pipewire.h>
#include <spa/buffer/meta.h>
#include <spa/param/buffers.h>
#include <spa/param/format-utils.h>
#include <spa/param/video/format-utils.h>
#include <spa/param/video/raw-utils.h>
#include <spa/pod/builder.h>
#include <spa/utils/result.h>

#include <drm_fourcc.h>
#include <gbm.h>
#include <libyuv.h>
#include <rockchip/rk_mpi.h>
#include <rockchip/rk_mpi_cmd.h>
#include <rockchip/rk_venc_cfg.h>
#include <rockchip/rk_venc_cmd.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <deque>
#include <mutex>
#include <string>
#include <sys/ioctl.h>
#include <thread>
#include <unordered_map>
#include <unistd.h>
#include <linux/dma-buf.h>

namespace {

struct Encoder {
    struct Slot {
        MppBuffer buffer = nullptr;
        MppFrame frame = nullptr;
        uint8_t *nv12 = nullptr;
        bool busy = false;
    };

    MppCtx ctx = nullptr;
    MppApi *api = nullptr;
    MppEncCfg cfg = nullptr;
    MppBufferGroup group = nullptr;
    std::array<Slot, 3> slots;
    std::mutex mutex;
    std::condition_variable ready;
    std::deque<size_t> submitted;
    std::thread output_thread;
    bool stopping = false;
    std::atomic<uint64_t> encoded{0};
    std::atomic<uint64_t> dropped{0};
    std::atomic<uint64_t> failed{0};
    int width;
    int height;
    int stride_width;
    int stride_height;
    int output_fd;

    Encoder(int w, int h, int fps, int bitrate, int fd)
        : width(w), height(h), stride_width((w + 15) & ~15),
          stride_height((h + 15) & ~15), output_fd(fd) {
        auto check = [](MPP_RET result, const char *what) {
            if (result != MPP_OK) {
                std::fprintf(stderr, "rkwebscr-dmabuf: %s failed: %d\n", what, result);
                std::exit(1);
            }
        };

        check(mpp_create(&ctx, &api), "mpp_create");
        check(mpp_init(ctx, MPP_CTX_ENC, MPP_VIDEO_CodingAVC), "mpp_init");
        RK_S64 output_timeout = MPP_POLL_BLOCK;
        check(api->control(ctx, MPP_SET_OUTPUT_TIMEOUT, &output_timeout),
              "MPP_SET_OUTPUT_TIMEOUT");
        check(mpp_enc_cfg_init(&cfg), "mpp_enc_cfg_init");
        check(api->control(ctx, MPP_ENC_GET_CFG, cfg), "MPP_ENC_GET_CFG");

        mpp_enc_cfg_set_s32(cfg, "prep:width", width);
        mpp_enc_cfg_set_s32(cfg, "prep:height", height);
        mpp_enc_cfg_set_s32(cfg, "prep:hor_stride", stride_width);
        mpp_enc_cfg_set_s32(cfg, "prep:ver_stride", stride_height);
        mpp_enc_cfg_set_s32(cfg, "prep:format", MPP_FMT_YUV420SP);
        mpp_enc_cfg_set_s32(cfg, "rc:mode", MPP_ENC_RC_MODE_CBR);
        mpp_enc_cfg_set_s32(cfg, "rc:fps_in_flex", 0);
        mpp_enc_cfg_set_s32(cfg, "rc:fps_in_num", fps);
        mpp_enc_cfg_set_s32(cfg, "rc:fps_in_denom", 1);
        mpp_enc_cfg_set_s32(cfg, "rc:fps_out_flex", 0);
        mpp_enc_cfg_set_s32(cfg, "rc:fps_out_num", fps);
        mpp_enc_cfg_set_s32(cfg, "rc:fps_out_denom", 1);
        mpp_enc_cfg_set_s32(cfg, "rc:bps_target", bitrate);
        mpp_enc_cfg_set_s32(cfg, "rc:bps_max", bitrate);
        mpp_enc_cfg_set_s32(cfg, "rc:bps_min", bitrate);
        mpp_enc_cfg_set_s32(cfg, "rc:gop", fps);
        mpp_enc_cfg_set_s32(cfg, "codec:type", MPP_VIDEO_CodingAVC);
        mpp_enc_cfg_set_s32(cfg, "h264:profile", 66);
        mpp_enc_cfg_set_s32(cfg, "h264:level", height <= 720 ? 32 : 42);
        mpp_enc_cfg_set_s32(cfg, "h264:cabac_en", 0);
        check(api->control(ctx, MPP_ENC_SET_CFG, cfg), "MPP_ENC_SET_CFG");

        MppEncHeaderMode header_mode = MPP_ENC_HEADER_MODE_EACH_IDR;
        check(api->control(ctx, MPP_ENC_SET_HEADER_MODE, &header_mode),
              "MPP_ENC_SET_HEADER_MODE");

        check(mpp_buffer_group_get_internal(&group, MPP_BUFFER_TYPE_DRM),
              "mpp_buffer_group_get_internal");
        size_t size = static_cast<size_t>(stride_width) * stride_height * 3 / 2;
        for (auto &slot : slots) {
            check(mpp_buffer_get(group, &slot.buffer, size), "mpp_buffer_get");
            check(mpp_frame_init(&slot.frame), "mpp_frame_init");
            mpp_frame_set_width(slot.frame, width);
            mpp_frame_set_height(slot.frame, height);
            mpp_frame_set_hor_stride(slot.frame, stride_width);
            mpp_frame_set_ver_stride(slot.frame, stride_height);
            mpp_frame_set_fmt(slot.frame, MPP_FMT_YUV420SP);
            mpp_frame_set_buffer(slot.frame, slot.buffer);
            slot.nv12 = static_cast<uint8_t *>(mpp_buffer_get_ptr(slot.buffer));
            if (!slot.nv12) {
                std::fprintf(stderr,
                             "rkwebscr-dmabuf: could not map MPP output buffer\n");
                std::exit(1);
            }
        }
        output_thread = std::thread(&Encoder::output_loop, this);
    }

    ~Encoder() {
        {
            std::lock_guard lock(mutex);
            stopping = true;
        }
        ready.notify_one();
        if (output_thread.joinable())
            output_thread.join();
        for (auto &slot : slots) {
            if (slot.frame)
                mpp_frame_deinit(&slot.frame);
            if (slot.buffer)
                mpp_buffer_put(slot.buffer);
        }
        if (group)
            mpp_buffer_group_put(group);
        if (cfg)
            mpp_enc_cfg_deinit(cfg);
        if (ctx) {
            mpp_destroy(ctx);
            ctx = nullptr;
        }
    }

    bool submit(const uint8_t *source, int source_stride, int64_t pts) {
        size_t index = slots.size();
        {
            std::lock_guard lock(mutex);
            for (size_t i = 0; i < slots.size(); ++i) {
                if (!slots[i].busy) {
                    slots[i].busy = true;
                    index = i;
                    break;
                }
            }
        }
        if (index == slots.size()) {
            dropped++;
            return false;
        }

        Slot &slot = slots[index];
        mpp_buffer_sync_begin(slot.buffer);
        int converted = libyuv::ARGBToNV12(
            source, source_stride, slot.nv12, stride_width,
            slot.nv12 + static_cast<size_t>(stride_width) * stride_height,
            stride_width, width, height);
        mpp_buffer_sync_end(slot.buffer);
        if (converted != 0) {
            std::fprintf(stderr, "rkwebscr-dmabuf: libyuv conversion failed: %d\n",
                         converted);
            release(index);
            failed++;
            return false;
        }

        mpp_frame_set_pts(slot.frame, pts);
        MPP_RET result = api->encode_put_frame(ctx, slot.frame);
        if (result != MPP_OK) {
            std::fprintf(stderr, "rkwebscr-dmabuf: MPP submit failed: %d\n",
                         result);
            release(index);
            failed++;
            return false;
        }
        {
            std::lock_guard lock(mutex);
            submitted.push_back(index);
        }
        ready.notify_one();
        return true;
    }

    void output_loop() {
        while (true) {
            size_t index;
            {
                std::unique_lock lock(mutex);
                ready.wait(lock, [this] { return stopping || !submitted.empty(); });
                if (stopping && submitted.empty())
                    return;
                index = submitted.front();
                submitted.pop_front();
            }

            MppPacket packet = nullptr;
            MPP_RET result = api->encode_get_packet(ctx, &packet);
            bool ok = result == MPP_OK && packet;
            if (ok) {
                const auto *data = static_cast<const uint8_t *>(
                    mpp_packet_get_pos(packet));
                ok = write_packet(data, mpp_packet_get_length(packet));
                mpp_packet_deinit(&packet);
            }
            if (ok)
                encoded++;
            else {
                std::fprintf(stderr, "rkwebscr-dmabuf: MPP output failed: %d\n",
                             result);
                failed++;
            }
            release(index);
        }
    }

    void release(size_t index) {
        std::lock_guard lock(mutex);
        slots[index].busy = false;
    }

    bool write_packet(const uint8_t *data, size_t size) {
        if (size == 0 || size > UINT32_MAX)
            return false;
        uint8_t header[4] = {
            static_cast<uint8_t>(size >> 24), static_cast<uint8_t>(size >> 16),
            static_cast<uint8_t>(size >> 8), static_cast<uint8_t>(size)
        };
        return write_all(header, sizeof(header)) && write_all(data, size);
    }

    bool write_all(const uint8_t *data, size_t size) {
        while (size) {
            ssize_t written = ::write(output_fd, data, size);
            if (written < 0) {
                if (errno == EINTR)
                    continue;
                return false;
            }
            data += written;
            size -= static_cast<size_t>(written);
        }
        return true;
    }
};

struct State {
    pw_main_loop *loop = nullptr;
    pw_stream *stream = nullptr;
    spa_hook stream_listener{};
    Encoder *encoder = nullptr;
    spa_video_info_raw format{};
    int drm_fd = -1;
    gbm_device *gbm = nullptr;
    struct SourceBuffer {
        gbm_bo *buffer;
        uint8_t *mapped;
        uint32_t stride;
        void *map_data;
    };
    std::unordered_map<int, SourceBuffer> source_buffers;
    int width;
    int height;
    int fps;
    int capture_fps;
    uint64_t frames = 0;
    std::chrono::steady_clock::time_point stats_at = std::chrono::steady_clock::now();

    ~State() {
        for (auto [fd, source] : source_buffers) {
            (void)fd;
            gbm_bo_unmap(source.buffer, source.map_data);
            gbm_bo_destroy(source.buffer);
        }
        if (gbm)
            gbm_device_destroy(gbm);
        if (drm_fd >= 0)
            close(drm_fd);
    }
};

void on_state_changed(void *data, pw_stream_state old_state,
                      pw_stream_state state, const char *error) {
    auto *self = static_cast<State *>(data);
    std::fprintf(stderr, "rkwebscr-dmabuf: PipeWire %s -> %s%s%s\n",
                 pw_stream_state_as_string(old_state),
                 pw_stream_state_as_string(state), error ? ": " : "",
                 error ? error : "");
    if (state == PW_STREAM_STATE_ERROR)
        pw_main_loop_quit(self->loop);
}

void on_param_changed(void *data, uint32_t id, const spa_pod *param) {
    auto *self = static_cast<State *>(data);
    if (!param || id != SPA_PARAM_Format)
        return;
    if (spa_format_video_raw_parse(param, &self->format) < 0) {
        std::fprintf(stderr, "rkwebscr-dmabuf: unsupported PipeWire video format\n");
        pw_main_loop_quit(self->loop);
        return;
    }

    uint8_t storage[1024];
    spa_pod_builder builder = SPA_POD_BUILDER_INIT(storage, sizeof(storage));
    const spa_pod *params[2];
    uint32_t data_types = 1u << SPA_DATA_DmaBuf;
    int stride = self->width * 4;
    params[0] = static_cast<const spa_pod *>(spa_pod_builder_add_object(
        &builder, SPA_TYPE_OBJECT_ParamBuffers, SPA_PARAM_Buffers,
        SPA_PARAM_BUFFERS_buffers, SPA_POD_CHOICE_RANGE_Int(4, 2, 8),
        SPA_PARAM_BUFFERS_blocks, SPA_POD_Int(1),
        SPA_PARAM_BUFFERS_size, SPA_POD_Int(stride * self->height),
        SPA_PARAM_BUFFERS_stride, SPA_POD_Int(stride),
        SPA_PARAM_BUFFERS_dataType, SPA_POD_CHOICE_FLAGS_Int(data_types)));
    params[1] = static_cast<const spa_pod *>(spa_pod_builder_add_object(
        &builder, SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta,
        SPA_PARAM_META_type, SPA_POD_Id(SPA_META_Header),
        SPA_PARAM_META_size, SPA_POD_Int(sizeof(spa_meta_header))));
    pw_stream_update_params(self->stream, params, 2);
}

void on_process(void *data) {
    auto *self = static_cast<State *>(data);
    pw_buffer *buffer = nullptr;
    pw_buffer *next = nullptr;
    while ((next = pw_stream_dequeue_buffer(self->stream))) {
        if (buffer)
            pw_stream_queue_buffer(self->stream, buffer);
        buffer = next;
    }
    if (!buffer)
        return;

    spa_buffer *spa_buffer = buffer->buffer;
    if (spa_buffer->n_datas == 0 || spa_buffer->datas[0].type != SPA_DATA_DmaBuf ||
        spa_buffer->datas[0].fd < 0) {
        std::fprintf(stderr, "rkwebscr-dmabuf: expected a DMA-BUF frame\n");
        self->encoder->failed++;
        pw_stream_queue_buffer(self->stream, buffer);
        return;
    }

    spa_data &plane = spa_buffer->datas[0];
    int stride = plane.chunk && plane.chunk->stride > 0
                     ? plane.chunk->stride / 4
                     : self->width;
    auto found = self->source_buffers.find(plane.fd);
    if (found == self->source_buffers.end()) {
        gbm_import_fd_data info{};
        info.fd = static_cast<int>(plane.fd);
        info.width = self->width;
        info.height = self->height;
        info.stride = stride * 4;
        info.format = DRM_FORMAT_XRGB8888;
        gbm_bo *imported = gbm_bo_import(self->gbm, GBM_BO_IMPORT_FD,
                                         &info, GBM_BO_USE_LINEAR);
        if (!imported) {
            std::fprintf(stderr, "rkwebscr-dmabuf: GBM import failed for fd %d\n",
                         info.fd);
            self->encoder->failed++;
            pw_stream_queue_buffer(self->stream, buffer);
            return;
        }
        uint32_t mapped_stride = 0;
        void *map_data = nullptr;
        auto *mapped = static_cast<uint8_t *>(gbm_bo_map(
            imported, 0, 0, self->width, self->height,
            GBM_BO_TRANSFER_READ, &mapped_stride, &map_data));
        if (!mapped) {
            std::fprintf(stderr, "rkwebscr-dmabuf: GBM map failed for fd %d\n",
                         static_cast<int>(plane.fd));
            gbm_bo_destroy(imported);
            self->encoder->failed++;
            pw_stream_queue_buffer(self->stream, buffer);
            return;
        }
        found = self->source_buffers.emplace(
            plane.fd, State::SourceBuffer{imported, mapped, mapped_stride,
                                          map_data}).first;
    }

    dma_buf_sync sync{DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ};
    ioctl(static_cast<int>(plane.fd), DMA_BUF_IOCTL_SYNC, &sync);
    size_t offset = plane.chunk ? plane.chunk->offset : 0;
    bool accepted = self->encoder->submit(found->second.mapped + offset,
                                          found->second.stride,
                                          static_cast<int64_t>(self->frames));
    sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
    ioctl(static_cast<int>(plane.fd), DMA_BUF_IOCTL_SYNC, &sync);
    if (accepted)
        self->frames++;
    pw_stream_queue_buffer(self->stream, buffer);

    auto now = std::chrono::steady_clock::now();
    double seconds = std::chrono::duration<double>(now - self->stats_at).count();
    if (seconds >= 1.0) {
        static uint64_t previous = 0;
        uint64_t encoded = self->encoder->encoded.load();
        std::fprintf(stderr,
                     "rkwebscr-dmabuf: %.1f fps, %llu encoded, %llu dropped, %llu failed\n",
                     (encoded - previous) / seconds,
                     static_cast<unsigned long long>(encoded),
                     static_cast<unsigned long long>(self->encoder->dropped.load()),
                     static_cast<unsigned long long>(self->encoder->failed.load()));
        previous = encoded;
        self->stats_at = now;
    }
}

const pw_stream_events stream_events = [] {
    pw_stream_events events{};
    events.version = PW_VERSION_STREAM_EVENTS;
    events.state_changed = on_state_changed;
    events.param_changed = on_param_changed;
    events.process = on_process;
    return events;
}();

int positive(const char *text, const char *name) {
    char *end = nullptr;
    long value = std::strtol(text, &end, 10);
    if (!text[0] || *end || value <= 0 || value > INT32_MAX) {
        std::fprintf(stderr, "invalid %s: %s\n", name, text);
        std::exit(2);
    }
    return static_cast<int>(value);
}

} // namespace

int main(int argc, char **argv) {
    if (argc != 7) {
        std::fprintf(stderr,
                     "usage: %s NODE_ID WIDTH HEIGHT FPS BITRATE OUTPUT_FD\n",
                     argv[0]);
        return 2;
    }
    int node_id = positive(argv[1], "node id");
    State state{};
    state.width = positive(argv[2], "width");
    state.height = positive(argv[3], "height");
    state.fps = positive(argv[4], "fps");
    const char *capture_fps = std::getenv("RKWEBSCR_CAPTURE_FPS");
    state.capture_fps = capture_fps ? positive(capture_fps, "capture fps")
                                    : state.fps;
    int bitrate = positive(argv[5], "bitrate");
    int output_fd = positive(argv[6], "output fd");

    const char *render_node = std::getenv("RKWEBSCR_RENDER_NODE");
    if (!render_node)
        render_node = "/dev/dri/renderD130";
    state.drm_fd = open(render_node, O_RDWR | O_CLOEXEC);
    if (state.drm_fd < 0) {
        std::fprintf(stderr, "rkwebscr-dmabuf: could not open %s: %s\n",
                     render_node, std::strerror(errno));
        return 1;
    }
    state.gbm = gbm_create_device(state.drm_fd);
    if (!state.gbm) {
        std::fprintf(stderr, "rkwebscr-dmabuf: could not create GBM device\n");
        return 1;
    }

    pw_init(&argc, &argv);
    Encoder encoder(state.width, state.height, state.fps, bitrate, output_fd);
    state.encoder = &encoder;
    state.loop = pw_main_loop_new(nullptr);
    if (!state.loop) {
        std::fprintf(stderr, "rkwebscr-dmabuf: could not create PipeWire loop\n");
        return 1;
    }

    pw_properties *properties = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Video", PW_KEY_MEDIA_CATEGORY, "Capture",
        PW_KEY_MEDIA_ROLE, "Screen", PW_KEY_APP_NAME, "rkwebscr-dmabuf", nullptr);
    state.stream = pw_stream_new_simple(pw_main_loop_get_loop(state.loop),
                                        "rkwebscr-dmabuf", properties,
                                        &stream_events, &state);
    if (!state.stream) {
        std::fprintf(stderr, "rkwebscr-dmabuf: could not create PipeWire stream\n");
        pw_main_loop_destroy(state.loop);
        return 1;
    }

    uint8_t storage[1024];
    spa_pod_builder builder = SPA_POD_BUILDER_INIT(storage, sizeof(storage));
    spa_pod_frame object;
    spa_rectangle size = SPA_RECTANGLE(static_cast<uint32_t>(state.width),
                                       static_cast<uint32_t>(state.height));
    spa_fraction capture_rate =
        SPA_FRACTION(static_cast<uint32_t>(state.capture_fps), 1);
    spa_pod_builder_push_object(&builder, &object, SPA_TYPE_OBJECT_Format,
                                SPA_PARAM_EnumFormat);
    spa_pod_builder_add(&builder,
                        SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
                        SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
                        SPA_FORMAT_VIDEO_format, SPA_POD_Id(SPA_VIDEO_FORMAT_BGRx),
                        0);
    spa_pod_builder_prop(&builder, SPA_FORMAT_VIDEO_modifier,
                         SPA_POD_PROP_FLAG_MANDATORY);
    spa_pod_builder_long(&builder, DRM_FORMAT_MOD_LINEAR);
    spa_pod_builder_add(&builder,
                        SPA_FORMAT_VIDEO_size, SPA_POD_Rectangle(&size),
                        SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&capture_rate),
                        SPA_FORMAT_VIDEO_maxFramerate, SPA_POD_Fraction(&capture_rate),
                        0);
    const spa_pod *format = static_cast<const spa_pod *>(
        spa_pod_builder_pop(&builder, &object));

    int result = pw_stream_connect(
        state.stream, PW_DIRECTION_INPUT, static_cast<uint32_t>(node_id),
        static_cast<pw_stream_flags>(PW_STREAM_FLAG_AUTOCONNECT |
                                     PW_STREAM_FLAG_RT_PROCESS),
        &format, 1);
    if (result < 0) {
        std::fprintf(stderr, "rkwebscr-dmabuf: PipeWire connect failed: %s\n",
                     spa_strerror(result));
        pw_stream_destroy(state.stream);
        pw_main_loop_destroy(state.loop);
        return 1;
    }

    std::fprintf(stderr,
                 "rkwebscr-dmabuf: requesting linear DMA-BUF %dx%d at %d fps\n",
                 state.width, state.height, state.capture_fps);
    pw_main_loop_run(state.loop);
    pw_stream_destroy(state.stream);
    pw_main_loop_destroy(state.loop);
    pw_deinit();
    return 0;
}
