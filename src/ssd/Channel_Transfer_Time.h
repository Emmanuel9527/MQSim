#ifndef CHANNEL_TRANSFER_TIME_H
#define CHANNEL_TRANSFER_TIME_H
#include <cstdint>
#include <stdexcept>
#include <limits>
namespace SSD_Components {
// Width is bytes per transfer. DDR moves two units per cycle.
// Round up the full burst once, retaining sub-ns cycle precision.
inline uint64_t Channel_transfer_ns(uint64_t bytes, uint64_t width, uint64_t rate_mt_s) {
    if (!width || !rate_mt_s || width > std::numeric_limits<uint64_t>::max()/2)
        throw std::invalid_argument("Invalid NAND channel width/rate");
    const uint64_t unit = 2*width;
    const uint64_t cycles = bytes/unit + (bytes%unit != 0);
    if (cycles > std::numeric_limits<uint64_t>::max()/2000)
        throw std::overflow_error("NAND transfer duration overflow");
    const uint64_t numerator = cycles*2000;
    return numerator/rate_mt_s + (numerator%rate_mt_s != 0);
}
}
#endif
