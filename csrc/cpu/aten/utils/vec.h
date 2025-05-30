#pragma once

#include <ATen/cpu/vec/functional.h>
#include <ATen/cpu/vec/vec.h>
#include <immintrin.h>
// namespace {
namespace torch_ipex {
namespace cpu {
using namespace at::vec;

template <
    typename scalar_t,
    typename std::enable_if_t<c10::is_reduced_floating_point_v<scalar_t>, int> =
        0>
inline Vectorized<scalar_t> convert_from_float_ext(
    const Vectorized<float>& a,
    const Vectorized<float>& b) {
  return at::vec::convert_from_float<scalar_t>(a, b);
}

#if defined(CPU_CAPABILITY_AVX512_BF16)

// at::vec::convert_from_float<>` from PyTorch doesn't have avx512-bf16
// intrinsics use native instruction for bfloat16->float32 conversion
template <>
inline Vectorized<at::BFloat16> convert_from_float_ext<at::BFloat16>(
    const Vectorized<float>& a,
    const Vectorized<float>& b) {
  return (__m512i)(_mm512_cvtne2ps_pbh(__m512(b), __m512(a)));
}

#define CVT_BF16_TO_FP32(a) \
  _mm512_castsi512_ps(_mm512_slli_epi32(_mm512_cvtepu16_epi32(a), 16))

#define CVT_FP16_TO_FP32(a) \
  _mm512_cvtps_ph(a, (_MM_FROUND_TO_NEAREST_INT | _MM_FROUND_NO_EXC))

#endif

// vector to scalar reduction
#if defined(CPU_CAPABILITY_AVX512) && 0
inline float vec_reduce_sum(const Vectorized<float>& a) {
  return _mm512_reduce_add_ps(__m512(a));
}

inline float vec_reduce_max(const Vectorized<float>& a) {
  return _mm512_reduce_max_ps(__m512(a));
}
#else
inline float vec_reduce_sum(const Vectorized<float>& a) {
  return vec_reduce_all(
      [](Vectorized<float>& x, Vectorized<float>& y) { return x + y; }, a);
}

inline float vec_reduce_max(const Vectorized<float>& a) {
  return vec_reduce_all(
      [](Vectorized<float>& x, Vectorized<float>& y) { return maximum(x, y); },
      a);
}
#endif

// } // anonymous namespace
} // namespace cpu
} // namespace torch_ipex
