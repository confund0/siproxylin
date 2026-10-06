/**
 * Video frames to the app over shared memory (Linux and Windows)
 *
 * The app makes a memfd, sets up the header and passes the fd number in
 * the env variable SIPROXYLIN_VIDEO_SHM_FD. The service maps it and writes
 * decoded frames into it. The app reads them on a Qt timer.
 * Windows: an unnamed file mapping instead of the memfd; the inherited
 * handle value is in SIPROXYLIN_VIDEO_SHM_HANDLE.
 * Python side: drunk_call_hook/video_shm.py (the layout and the constants
 * must match).
 *
 * Layout (little-endian, native alignment):
 * - Header at offset 0 (64 bytes):
 *     u32 magic 0x46565053 ("SPVF"), u32 version 1, u32 stream_count 2,
 *     u32 slot_count 3, u32 max_width 960, u32 max_height 960,
 *     u64 slot_bytes (960*960*4), u64 data_offset
 * - Stream control blocks, 64 bytes each, right after the header
 *   (stream 0 = remote video, stream 1 = self-view):
 *     u32 latest (slot of the newest ready frame, 0xFFFFFFFF = none),
 *     u32 reader (slot the app draws now, 0xFFFFFFFF = none; only the app
 *                 writes it),
 *     u32 generation (the service adds 1 when a video stream starts),
 *     u32 active (1 while video flows, 0 after stop)
 * - Slot headers, 64 bytes each, after the control blocks, for stream s
 *   slot i at index s*slot_count + i:
 *     u64 seq (odd while being written, even when ready), u32 width,
 *     u32 height, u32 stride, u32 generation, u64 pts_ns
 * - Pixel data at data_offset (4096-aligned): stream s slot i at
 *   data_offset + (s*slot_count + i)*slot_bytes. Format RGBx.
 *
 * Writer (this service), triple buffering: pick a slot that is not latest
 * and not reader. Set its seq odd, then read reader again; if it now is the
 * chosen slot, set seq back and take the other free slot. Copy the frame,
 * fill the slot header, set seq even (release), then latest = slot (release).
 */

#ifndef VIDEO_SHM_H
#define VIDEO_SHM_H

#include <cstddef>
#include <cstdint>
#include <mutex>

namespace drunk_call {

class VideoShm {
public:
    static constexpr uint32_t kMagic = 0x46565053;  // "SPVF"
    static constexpr uint32_t kVersion = 1;
    static constexpr uint32_t kStreamCount = 2;
    static constexpr uint32_t kSlotCount = 3;
    static constexpr uint32_t kMaxWidth = 960;
    static constexpr uint32_t kMaxHeight = 960;
    static constexpr uint64_t kSlotBytes = uint64_t(kMaxWidth) * kMaxHeight * 4;
    static constexpr uint32_t kNone = 0xFFFFFFFF;
    static constexpr uint32_t kStreamRemote = 0;
    static constexpr uint32_t kStreamSelf = 1;

    static constexpr size_t kHeaderSize = 64;
    static constexpr size_t kControlSize = 64;
    static constexpr size_t kSlotHeaderSize = 64;

    // Map the memfd from SIPROXYLIN_VIDEO_SHM_FD (Windows: the file mapping
    // from SIPROXYLIN_VIDEO_SHM_HANDLE). Logs and returns false when it is
    // missing or not valid; the service then runs without it.
    static bool init_from_env();

    // The mapped writer, or nullptr when there is no shared memory.
    static VideoShm* instance();

    // A new video stream starts: generation + 1, latest = none,
    // active = 1. Only frames from this owner are written after this.
    void begin_stream(uint32_t stream, const void *owner);

    // Write one RGBx frame. Returns false when the frame is skipped.
    bool write_frame(uint32_t stream, const void *owner, const uint8_t *data,
                     uint32_t width, uint32_t height, uint32_t stride,
                     uint64_t pts_ns);

    // The stream of this owner ends: active = 0, latest = none.
    void end_stream(uint32_t stream, const void *owner);

private:
    VideoShm(uint8_t *base, uint64_t data_offset);

    uint32_t *control_field(uint32_t stream, size_t offset) const;
    uint8_t *slot_header(uint32_t stream, uint32_t slot) const;
    uint8_t *slot_data(uint32_t stream, uint32_t slot) const;

    uint8_t *base_;
    uint64_t data_offset_;
    const void *owners_[kStreamCount];
    uint32_t generations_[kStreamCount];
    std::mutex mutex_;
};

} // namespace drunk_call

#endif // VIDEO_SHM_H
