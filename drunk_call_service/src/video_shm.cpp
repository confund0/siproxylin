/**
 * Video frames to the app over shared memory (Linux only)
 *
 * Layout and protocol: see video_shm.h.
 */

#include "video_shm.h"
#include "logger.h"
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace drunk_call {

namespace {

VideoShm *g_video_shm = nullptr;

// Control block field offsets
constexpr size_t kCtlLatest = 0;
constexpr size_t kCtlReader = 4;
constexpr size_t kCtlGeneration = 8;
constexpr size_t kCtlActive = 12;

// Slot header field offsets
constexpr size_t kSlotSeq = 0;
constexpr size_t kSlotWidth = 8;
constexpr size_t kSlotHeight = 12;
constexpr size_t kSlotStride = 16;
constexpr size_t kSlotGeneration = 20;
constexpr size_t kSlotPts = 24;

uint32_t read_u32(const uint8_t *p) {
    uint32_t v;
    std::memcpy(&v, p, sizeof(v));
    return v;
}

uint64_t read_u64(const uint8_t *p) {
    uint64_t v;
    std::memcpy(&v, p, sizeof(v));
    return v;
}

} // namespace

bool VideoShm::init_from_env() {
    const char *env = std::getenv("SIPROXYLIN_VIDEO_SHM_FD");
    if (!env || !*env) {
        LOG_INFO("[VideoShm] SIPROXYLIN_VIDEO_SHM_FD not set: remote video goes to autovideosink");
        return false;
    }

    char *end = nullptr;
    errno = 0;
    long fd = std::strtol(env, &end, 10);
    if (errno != 0 || end == env || *end != '\0' || fd < 0 || fd > 65535) {
        LOG_WARN("[VideoShm] Bad SIPROXYLIN_VIDEO_SHM_FD value: '{}'", env);
        return false;
    }

    struct stat st;
    if (fstat(static_cast<int>(fd), &st) != 0) {
        LOG_WARN("[VideoShm] fstat on fd {} failed: {}", fd, std::strerror(errno));
        return false;
    }
    size_t size = static_cast<size_t>(st.st_size);

    const size_t meta_size = kHeaderSize + kStreamCount * kControlSize
                             + kStreamCount * kSlotCount * kSlotHeaderSize;
    if (size < meta_size) {
        LOG_WARN("[VideoShm] Shared memory too small: {} bytes", size);
        close(static_cast<int>(fd));
        return false;
    }

    void *mem = mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, static_cast<int>(fd), 0);
    // The mapping stays valid after close
    close(static_cast<int>(fd));
    if (mem == MAP_FAILED) {
        LOG_WARN("[VideoShm] mmap failed: {}", std::strerror(errno));
        return false;
    }
    uint8_t *base = static_cast<uint8_t*>(mem);

    uint32_t magic = read_u32(base + 0);
    uint32_t version = read_u32(base + 4);
    uint32_t stream_count = read_u32(base + 8);
    uint32_t slot_count = read_u32(base + 12);
    uint32_t max_width = read_u32(base + 16);
    uint32_t max_height = read_u32(base + 20);
    uint64_t slot_bytes = read_u64(base + 24);
    uint64_t data_offset = read_u64(base + 32);

    bool ok = magic == kMagic && version == kVersion
              && stream_count == kStreamCount && slot_count == kSlotCount
              && max_width == kMaxWidth && max_height == kMaxHeight
              && slot_bytes == kSlotBytes
              && data_offset % 4096 == 0 && data_offset >= meta_size
              && data_offset + uint64_t(kStreamCount) * kSlotCount * kSlotBytes <= size;
    if (!ok) {
        LOG_WARN("[VideoShm] Shared memory header does not match (magic={:#x}, version={}, "
                 "streams={}, slots={}, max={}x{}, slot_bytes={}, data_offset={}, size={})",
                 magic, version, stream_count, slot_count, max_width, max_height,
                 slot_bytes, data_offset, size);
        munmap(mem, size);
        return false;
    }

    g_video_shm = new VideoShm(base, data_offset);
    LOG_INFO("[VideoShm] Mapped shared memory for video: {} bytes, {} slots of {}x{} RGBx",
             size, kSlotCount, kMaxWidth, kMaxHeight);
    return true;
}

VideoShm* VideoShm::instance() {
    return g_video_shm;
}

VideoShm::VideoShm(uint8_t *base, uint64_t data_offset)
    : base_(base)
    , data_offset_(data_offset)
{
    for (uint32_t s = 0; s < kStreamCount; s++) {
        owners_[s] = nullptr;
        generations_[s] = __atomic_load_n(control_field(s, kCtlGeneration), __ATOMIC_ACQUIRE);
    }
}

uint32_t *VideoShm::control_field(uint32_t stream, size_t offset) const {
    return reinterpret_cast<uint32_t*>(base_ + kHeaderSize + stream * kControlSize + offset);
}

uint8_t *VideoShm::slot_header(uint32_t stream, uint32_t slot) const {
    return base_ + kHeaderSize + kStreamCount * kControlSize
           + (stream * kSlotCount + slot) * kSlotHeaderSize;
}

uint8_t *VideoShm::slot_data(uint32_t stream, uint32_t slot) const {
    return base_ + data_offset_ + (uint64_t(stream) * kSlotCount + slot) * kSlotBytes;
}

void VideoShm::begin_stream(uint32_t stream, const void *owner) {
    if (stream >= kStreamCount) {
        return;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    owners_[stream] = owner;
    generations_[stream] += 1;
    __atomic_store_n(control_field(stream, kCtlLatest), kNone, __ATOMIC_RELEASE);
    __atomic_store_n(control_field(stream, kCtlGeneration), generations_[stream], __ATOMIC_RELEASE);
    __atomic_store_n(control_field(stream, kCtlActive), 1u, __ATOMIC_RELEASE);
    LOG_INFO("[VideoShm] Stream {} started, generation {}", stream, generations_[stream]);
}

bool VideoShm::write_frame(uint32_t stream, const void *owner, const uint8_t *data,
                           uint32_t width, uint32_t height, uint32_t stride,
                           uint64_t pts_ns) {
    if (stream >= kStreamCount || !data) {
        return false;
    }
    if (width == 0 || height == 0 || width > kMaxWidth || height > kMaxHeight
        || stride < width * 4) {
        return false;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (owners_[stream] != owner || owner == nullptr) {
        return false;
    }

    uint32_t latest = __atomic_load_n(control_field(stream, kCtlLatest), __ATOMIC_ACQUIRE);
    uint32_t reader = __atomic_load_n(control_field(stream, kCtlReader), __ATOMIC_ACQUIRE);

    // 3 slots, at most 2 are taken: a free slot always exists
    uint32_t slot = 0;
    while (slot == latest || slot == reader) {
        slot++;
    }

    uint64_t *seq_ptr = reinterpret_cast<uint64_t*>(slot_header(stream, slot) + kSlotSeq);
    uint64_t seq = __atomic_load_n(seq_ptr, __ATOMIC_RELAXED) & ~uint64_t(1);
    __atomic_store_n(seq_ptr, seq + 1, __ATOMIC_SEQ_CST);

    // The app may have marked this slot in the meantime: take the other free one
    if (__atomic_load_n(control_field(stream, kCtlReader), __ATOMIC_SEQ_CST) == slot) {
        __atomic_store_n(seq_ptr, seq, __ATOMIC_RELEASE);
        uint32_t taken = slot;
        slot = 0;
        while (slot == latest || slot == taken) {
            slot++;
        }
        seq_ptr = reinterpret_cast<uint64_t*>(slot_header(stream, slot) + kSlotSeq);
        seq = __atomic_load_n(seq_ptr, __ATOMIC_RELAXED) & ~uint64_t(1);
        __atomic_store_n(seq_ptr, seq + 1, __ATOMIC_SEQ_CST);
    }

    // Copy rows with a tight stride (width * 4)
    const uint32_t out_stride = width * 4;
    uint8_t *dst = slot_data(stream, slot);
    for (uint32_t y = 0; y < height; y++) {
        std::memcpy(dst + uint64_t(y) * out_stride, data + uint64_t(y) * stride, out_stride);
    }

    uint8_t *hdr = slot_header(stream, slot);
    __atomic_store_n(reinterpret_cast<uint32_t*>(hdr + kSlotWidth), width, __ATOMIC_RELAXED);
    __atomic_store_n(reinterpret_cast<uint32_t*>(hdr + kSlotHeight), height, __ATOMIC_RELAXED);
    __atomic_store_n(reinterpret_cast<uint32_t*>(hdr + kSlotStride), out_stride, __ATOMIC_RELAXED);
    __atomic_store_n(reinterpret_cast<uint32_t*>(hdr + kSlotGeneration), generations_[stream], __ATOMIC_RELAXED);
    __atomic_store_n(reinterpret_cast<uint64_t*>(hdr + kSlotPts), pts_ns, __ATOMIC_RELAXED);

    __atomic_store_n(seq_ptr, seq + 2, __ATOMIC_RELEASE);
    __atomic_store_n(control_field(stream, kCtlLatest), slot, __ATOMIC_RELEASE);
    return true;
}

void VideoShm::end_stream(uint32_t stream, const void *owner) {
    if (stream >= kStreamCount) {
        return;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (owners_[stream] != owner || owner == nullptr) {
        return;
    }
    owners_[stream] = nullptr;
    __atomic_store_n(control_field(stream, kCtlActive), 0u, __ATOMIC_RELEASE);
    __atomic_store_n(control_field(stream, kCtlLatest), kNone, __ATOMIC_RELEASE);
    LOG_INFO("[VideoShm] Stream {} stopped", stream);
}

} // namespace drunk_call
