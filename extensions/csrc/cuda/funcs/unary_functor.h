#pragma once

#include <cuda.h>
#include <cuda_bf16.h>
#include <cuda_fp16.h>
#include <cuda_runtime.h>

#include <functional>

#include "../utils/micros.h"

namespace colossalAI {
namespace cuda {
namespace funcs {

template <typename T>
inline __device__ void zero(T& dst) {
  constexpr int WORDS = sizeof(T) / 4;
  union {
    T raw;
    uint32_t words[WORDS];
  } tmp;

#pragma unroll
  for (int ii = 0; ii < WORDS; ii++) {
    tmp.words[ii] = 0u;
  }
  dst = tmp.raw;
}

// Note(LiuYang): As a retrieved table to check which operation is supported
// already
enum class UnaryOpType { kLog2Ceil = 0, kAbs, kSum };

// Note(LiuYang): Implementation of common and simple unary operators should be
// placed here, otherwise, they should be placed in a new file under functors
// dir.
template <typename From, typename To, UnaryOpType op_type>
struct UnaryOpFunctor;

#define COLOSSAL_UNARY_FUNCTOR_SPECIALIZATION(                  \
    FROM, TO, UNARY_OP_TYPE, FUNCTION_MODIFIER, STMTS, ARGS...) \
  template <ARGS>                                               \
  struct UnaryOpFunctor<FROM, TO, UNARY_OP_TYPE>                \
      : public std::unary_function<FROM, TO> {                  \
    FUNCTION_MODIFIER TO operator()(FROM val) STMTS             \
  };

COLOSSAL_UNARY_FUNCTOR_SPECIALIZATION(
    T, T, UnaryOpType::kAbs, HOSTDEVICE, { return std::abs(val); }, typename T)

COLOSSAL_UNARY_FUNCTOR_SPECIALIZATION(int, int, UnaryOpType::kLog2Ceil,
                                      HOSTDEVICE, {
                                        int log2_value = 0;
                                        while ((1 << log2_value) < val)
                                          ++log2_value;
                                        return log2_value;
                                      })

COLOSSAL_UNARY_FUNCTOR_SPECIALIZATION(float2, float, UnaryOpType::kSum, DEVICE,
                                      { return val.x + val.y; })

COLOSSAL_UNARY_FUNCTOR_SPECIALIZATION(float4, float, UnaryOpType::kSum, DEVICE,
                                      { return val.x + val.y + val.z + val.w; })

COLOSSAL_UNARY_FUNCTOR_SPECIALIZATION(float4_, float, UnaryOpType::kSum, DEVICE,
                                      {
                                        return val.x.x + val.x.y + val.y.x +
                                               val.y.y;
                                      })

COLOSSAL_UNARY_FUNCTOR_SPECIALIZATION(float8_, float, UnaryOpType::kSum, DEVICE,
                                      {
                                        return val.x.x + val.x.y + val.y.x +
                                               val.y.y + val.z.x + val.z.y +
                                               val.w.x + val.w.y;
                                      })

#undef COLOSSAL_UARY_FUNCTOR_SPECIALIZATION

}  // namespace funcs
}  // namespace cuda
}  // namespace colossalAI
