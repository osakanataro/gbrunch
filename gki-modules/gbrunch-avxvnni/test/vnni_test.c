// SPDX-License-Identifier: GPL-2.0
/*
 * User-space test for gbrunch_avxvnni.ko. Build: gcc -O2 -static -mavx2 -mavxvnni vnni_test.c
 * Run on a CPU without AVX-VNNI with the module loaded: every VEX VNNI form must match the C
 * reference, untouched registers must survive, and an unrelated #UD must still raise SIGILL.
 */
#include <immintrin.h>
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

static int fails;
static uint32_t seed = 12345;
static uint32_t rnd(void) { seed = seed * 1103515245u + 12345u; return seed ^ (seed >> 13); }

static void ref(int op, const uint32_t *s1, const uint32_t *s2, uint32_t *d, int n)
{
	for (int i = 0; i < n; i++) {
		int64_t sum;
		if (op < 2) {
			const uint8_t *a = (const uint8_t *)&s1[i]; const int8_t *b = (const int8_t *)&s2[i];
			sum = (int64_t)a[0] * b[0] + (int64_t)a[1] * b[1] + (int64_t)a[2] * b[2] + (int64_t)a[3] * b[3];
		} else {
			const int16_t *a = (const int16_t *)&s1[i]; const int16_t *b = (const int16_t *)&s2[i];
			sum = (int64_t)a[0] * b[0] + (int64_t)a[1] * b[1];
		}
		int64_t r = (int64_t)(int32_t)d[i] + sum;
		if (op & 1) r = r > INT32_MAX ? INT32_MAX : r < INT32_MIN ? INT32_MIN : r;
		d[i] = (uint32_t)r;
	}
}

static void fill(void *p, int n) { uint32_t *w = p; for (int i = 0; i < n / 4; i++) w[i] = rnd(); }

static void check(const char *name, const void *got, const void *want, int n)
{
	if (memcmp(got, want, n)) {
		fails++;
		printf("FAIL %s\n  got ", name);
		for (int i = 0; i < n; i++) printf("%02x", ((const uint8_t *)got)[i]);
		printf("\n  want ");
		for (int i = 0; i < n; i++) printf("%02x", ((const uint8_t *)want)[i]);
		printf("\n");
	} else {
		printf("ok   %s\n", name);
	}
}

#define REG_TEST(name, insn, T, LOADU, STOREU, N, OP) do { \
	uint8_t a[32], b[32], d[32], want[32]; \
	fill(a, 32); fill(b, 32); fill(d, 32); memcpy(want, d, 32); \
	T va = LOADU((const T *)a), vb = LOADU((const T *)b), vd = LOADU((const T *)d); \
	asm volatile("%{vex%} " insn " %2, %1, %0" : "+x"(vd) : "x"(va), "x"(vb)); \
	STOREU((T *)d, vd); ref(OP, (uint32_t *)a, (uint32_t *)b, (uint32_t *)want, N); \
	check(name " reg", d, want, N * 4); \
	fill(d, 32); memcpy(want, d, 32); vd = LOADU((const T *)d); \
	asm volatile("%{vex%} " insn " %2, %1, %0" : "+x"(vd) : "x"(va), "m"(*(const T *)b)); \
	STOREU((T *)d, vd); ref(OP, (uint32_t *)a, (uint32_t *)b, (uint32_t *)want, N); \
	check(name " mem", d, want, N * 4); \
} while (0)

static uint8_t gdata[32] __attribute__((aligned(32)));

static sigjmp_buf jb;
static void on_sigill(int sig) { siglongjmp(jb, 1); }

int main(void)
{
	setvbuf(stdout, NULL, _IONBF, 0);
	printf("vnni_test start\n");
	REG_TEST("vpdpbusd  ymm", "vpdpbusd",  __m256i, _mm256_loadu_si256, _mm256_storeu_si256, 8, 0);
	REG_TEST("vpdpbusds ymm", "vpdpbusds", __m256i, _mm256_loadu_si256, _mm256_storeu_si256, 8, 1);
	REG_TEST("vpdpwssd  ymm", "vpdpwssd",  __m256i, _mm256_loadu_si256, _mm256_storeu_si256, 8, 2);
	REG_TEST("vpdpwssds ymm", "vpdpwssds", __m256i, _mm256_loadu_si256, _mm256_storeu_si256, 8, 3);
	REG_TEST("vpdpbusd  xmm", "vpdpbusd",  __m128i, _mm_loadu_si128, _mm_storeu_si128, 4, 0);
	REG_TEST("vpdpbusds xmm", "vpdpbusds", __m128i, _mm_loadu_si128, _mm_storeu_si128, 4, 1);
	REG_TEST("vpdpwssd  xmm", "vpdpwssd",  __m128i, _mm_loadu_si128, _mm_storeu_si128, 4, 2);
	REG_TEST("vpdpwssds xmm", "vpdpwssds", __m128i, _mm_loadu_si128, _mm_storeu_si128, 4, 3);

	/* Saturation on purpose: 255*127*4 added to a value near INT32_MAX. */
	{
		uint8_t a[32], b[32], d[32], want[32];
		memset(a, 0xff, 32); memset(b, 0x7f, 32);
		for (int i = 0; i < 8; i++) ((int32_t *)d)[i] = INT32_MAX - 100;
		memcpy(want, d, 32);
		__m256i va = _mm256_loadu_si256((__m256i *)a), vb = _mm256_loadu_si256((__m256i *)b), vd = _mm256_loadu_si256((__m256i *)d);
		asm volatile("%{vex%} vpdpbusds %2, %1, %0" : "+x"(vd) : "x"(va), "x"(vb));
		_mm256_storeu_si256((__m256i *)d, vd); ref(1, (uint32_t *)a, (uint32_t *)b, (uint32_t *)want, 8);
		check("vpdpbusds saturate", d, want, 32);
		for (int i = 0; i < 8; i++) ((int32_t *)want)[i] = INT32_MAX;
		check("vpdpbusds saturate = INT32_MAX", d, want, 32);
	}

	/* Addressing forms: base+index*4+disp8, disp32, RIP-relative, high registers. */
	{
		uint8_t a[32], big[4096], d[32], want[32];
		fill(a, 32); fill(big, 4096); fill(d, 32); memcpy(want, d, 32);
		long idx = 9;
		__m256i va = _mm256_loadu_si256((__m256i *)a), vd = _mm256_loadu_si256((__m256i *)d);
		asm volatile("%{vex%} vpdpbusd 0x40(%1,%2,4), %3, %0" : "+x"(vd) : "r"(big), "r"(idx), "x"(va));
		_mm256_storeu_si256((__m256i *)d, vd); ref(0, (uint32_t *)a, (uint32_t *)(big + 0x40 + idx * 4), (uint32_t *)want, 8);
		check("vpdpbusd [base+index*4+disp8]", d, want, 32);

		fill(d, 32); memcpy(want, d, 32); vd = _mm256_loadu_si256((__m256i *)d);
		asm volatile("%{vex%} vpdpwssd 0x800(%1), %2, %0" : "+x"(vd) : "r"(big), "x"(va));
		_mm256_storeu_si256((__m256i *)d, vd); ref(2, (uint32_t *)a, (uint32_t *)(big + 0x800), (uint32_t *)want, 8);
		check("vpdpwssd [base+disp32]", d, want, 32);

		fill(gdata, 32); fill(d, 32); memcpy(want, d, 32); vd = _mm256_loadu_si256((__m256i *)d);
		asm volatile("%{vex%} vpdpbusds gdata(%%rip), %1, %0" : "+x"(vd) : "x"(va));
		_mm256_storeu_si256((__m256i *)d, vd); ref(1, (uint32_t *)a, (uint32_t *)gdata, (uint32_t *)want, 8);
		check("vpdpbusds [rip+disp32]", d, want, 32);

		fill(d, 32); memcpy(want, d, 32);
		register __m256i rd asm("xmm13") = _mm256_loadu_si256((__m256i *)d);
		register __m256i ra asm("xmm11") = _mm256_loadu_si256((__m256i *)a);
		register __m256i rb asm("xmm14") = _mm256_loadu_si256((__m256i *)big);
		asm volatile("%{vex%} vpdpwssds %2, %1, %0" : "+x"(rd) : "x"(ra), "x"(rb));
		_mm256_storeu_si256((__m256i *)d, rd); ref(3, (uint32_t *)a, (uint32_t *)big, (uint32_t *)want, 8);
		check("vpdpwssds ymm13, ymm11, ymm14", d, want, 32);

		/* Reload after the calls above: GCC's vzeroupper around calls clears the upper halves
		 * of register variables held in ymm registers. */
		fill(d, 32); memcpy(want, d, 32); rd = _mm256_loadu_si256((__m256i *)d);
		ra = _mm256_loadu_si256((__m256i *)a);
		register long r12 asm("r12") = (long)big;
		register long r9 asm("r9") = 3;
		asm volatile("%{vex%} vpdpbusd 8(%1,%2,8), %3, %0" : "+x"(rd) : "r"(r12), "r"(r9), "x"(ra));
		_mm256_storeu_si256((__m256i *)d, rd); ref(0, (uint32_t *)a, (uint32_t *)(big + 8 + 3 * 8), (uint32_t *)want, 8);
		check("vpdpbusd ymm13, ymm11, [r12+r9*8+8]", d, want, 32);
	}

	/* All 16 ymm loaded with patterns; one instruction; everything else must be intact,
	 * and an xmm-form destination must have its upper 128 bits cleared. */
	{
		uint8_t in[16 * 32], out[16 * 32], want[16 * 32];
		fill(in, sizeof in); memcpy(want, in, sizeof in);
		asm volatile(
			"vmovdqu 0(%0), %%ymm0\n vmovdqu 32(%0), %%ymm1\n vmovdqu 64(%0), %%ymm2\n vmovdqu 96(%0), %%ymm3\n"
			"vmovdqu 128(%0), %%ymm4\n vmovdqu 160(%0), %%ymm5\n vmovdqu 192(%0), %%ymm6\n vmovdqu 224(%0), %%ymm7\n"
			"vmovdqu 256(%0), %%ymm8\n vmovdqu 288(%0), %%ymm9\n vmovdqu 320(%0), %%ymm10\n vmovdqu 352(%0), %%ymm11\n"
			"vmovdqu 384(%0), %%ymm12\n vmovdqu 416(%0), %%ymm13\n vmovdqu 448(%0), %%ymm14\n vmovdqu 480(%0), %%ymm15\n"
			"%{vex%} vpdpbusd %%ymm7, %%ymm5, %%ymm3\n"
			"%{vex%} vpdpwssd %%xmm2, %%xmm4, %%xmm9\n"
			"vmovdqu %%ymm0, 0(%1)\n vmovdqu %%ymm1, 32(%1)\n vmovdqu %%ymm2, 64(%1)\n vmovdqu %%ymm3, 96(%1)\n"
			"vmovdqu %%ymm4, 128(%1)\n vmovdqu %%ymm5, 160(%1)\n vmovdqu %%ymm6, 192(%1)\n vmovdqu %%ymm7, 224(%1)\n"
			"vmovdqu %%ymm8, 256(%1)\n vmovdqu %%ymm9, 288(%1)\n vmovdqu %%ymm10, 320(%1)\n vmovdqu %%ymm11, 352(%1)\n"
			"vmovdqu %%ymm12, 384(%1)\n vmovdqu %%ymm13, 416(%1)\n vmovdqu %%ymm14, 448(%1)\n vmovdqu %%ymm15, 480(%1)\n"
			: : "r"(in), "r"(out)
			: "memory", "xmm0", "xmm1", "xmm2", "xmm3", "xmm4", "xmm5", "xmm6", "xmm7", "xmm8", "xmm9",
			  "xmm10", "xmm11", "xmm12", "xmm13", "xmm14", "xmm15");
		ref(0, (uint32_t *)(in + 5 * 32), (uint32_t *)(in + 7 * 32), (uint32_t *)(want + 3 * 32), 8);
		ref(2, (uint32_t *)(in + 4 * 32), (uint32_t *)(in + 2 * 32), (uint32_t *)(want + 9 * 32), 4);
		memset(want + 9 * 32 + 16, 0, 16);
		check("16 registers preserved, ymm3 and xmm9 updated", out, want, sizeof out);
	}

	/* An unrelated #UD must still be SIGILL. */
	{
		struct sigaction sa = { .sa_handler = on_sigill };
		sigaction(SIGILL, &sa, NULL);
		if (sigsetjmp(jb, 1) == 0) {
			asm volatile("ud2");
			fails++; printf("FAIL ud2 did not raise SIGILL\n");
		} else {
			printf("ok   ud2 still raises SIGILL\n");
		}
	}

	/* Cost per emulated instruction. */
	{
		struct timespec t0, t1;
		__m256i va = _mm256_set1_epi8(1), vb = _mm256_set1_epi8(2), vd = _mm256_setzero_si256();
		int n = 100000;
		clock_gettime(CLOCK_MONOTONIC, &t0);
		for (int i = 0; i < n; i++)
			asm volatile("%{vex%} vpdpbusd %2, %1, %0" : "+x"(vd) : "x"(va), "x"(vb));
		clock_gettime(CLOCK_MONOTONIC, &t1);
		double ns = ((t1.tv_sec - t0.tv_sec) * 1e9 + (t1.tv_nsec - t0.tv_nsec)) / n;
		uint32_t r[8]; _mm256_storeu_si256((__m256i *)r, vd);
		printf("%s %d instructions: %.0f ns each (result %u, want %u)\n", r[0] == (uint32_t)(8 * n) ? "ok  " : "FAIL",
		       n, ns, r[0], 8 * n);
		if (r[0] != (uint32_t)(8 * n)) fails++;
	}
	printf("vnni_test %s (%d failures)\n", fails ? "FAILED" : "PASSED", fails);
	return fails ? 1 : 0;
}
