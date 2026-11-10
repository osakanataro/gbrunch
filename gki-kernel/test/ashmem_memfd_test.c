// SPDX-License-Identifier: GPL-2.0
/*
 * GBRUNCH: check the memfd ashmem shim of a replacement kernel (gki-kernel/patches/0001).
 * Android's libcutils, with sys.use_memfd=true, creates ashmem regions as memfds and then uses the
 * ashmem ioctls on them (size, name, protection mask, pinning). Without CONFIG_ASHMEM (Rust) these
 * ioctls come from mm/memfd-ashmem-shim.c. Run by tools/test_profile_kernel_qemu.py; prints
 * "ashmem_memfd_test PASSED" or FAIL lines. Static build: gcc -O2 -static.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

#define ASHMEM_NAME_LEN 256
#define __ASHMEMIOC 0x77
#define ASHMEM_SET_NAME _IOW(__ASHMEMIOC, 1, char[ASHMEM_NAME_LEN])
#define ASHMEM_GET_NAME _IOR(__ASHMEMIOC, 2, char[ASHMEM_NAME_LEN])
#define ASHMEM_SET_SIZE _IOW(__ASHMEMIOC, 3, size_t)
#define ASHMEM_GET_SIZE _IO(__ASHMEMIOC, 4)
#define ASHMEM_SET_PROT_MASK _IOW(__ASHMEMIOC, 5, unsigned long)
#define ASHMEM_GET_PROT_MASK _IO(__ASHMEMIOC, 6)
#define ASHMEM_GET_PIN_STATUS _IO(__ASHMEMIOC, 9)
#define ASHMEM_IS_PINNED 1

static int fails;
#define CHECK(cond, ...) do { if (!(cond)) { fails++; printf("FAIL " __VA_ARGS__); printf("\n"); } else { printf("ok   " __VA_ARGS__); printf("\n"); } } while (0)

int main(void)
{
	char name[ASHMEM_NAME_LEN] = "";
	int fd = syscall(SYS_memfd_create, "gbrunch_test", MFD_CLOEXEC | MFD_ALLOW_SEALING);

	setvbuf(stdout, NULL, _IONBF, 0);
	printf("ashmem_memfd_test start\n");
	CHECK(access("/dev/ashmem", F_OK) != 0, "/dev/ashmem absent (memfd path expected)");
	CHECK(fd >= 0, "memfd_create");
	CHECK(ftruncate(fd, 8192) == 0, "ftruncate 8192");
	CHECK(ioctl(fd, ASHMEM_GET_SIZE) == 8192, "ASHMEM_GET_SIZE on memfd = 8192 (shim answers)");
	CHECK(ioctl(fd, ASHMEM_GET_NAME, name) == 0 && strcmp(name, "gbrunch_test") == 0,
	      "ASHMEM_GET_NAME = memfd name (%s)", name);
	CHECK(ioctl(fd, ASHMEM_GET_PROT_MASK) == (PROT_READ | PROT_WRITE | PROT_EXEC),
	      "ASHMEM_GET_PROT_MASK = rwx before sealing");
	CHECK(ioctl(fd, ASHMEM_SET_PROT_MASK, (unsigned long)PROT_READ) == 0, "ASHMEM_SET_PROT_MASK read-only");
	CHECK((fcntl(fd, F_GET_SEALS) & F_SEAL_FUTURE_WRITE), "read-only mask became F_SEAL_FUTURE_WRITE");
	CHECK(ioctl(fd, ASHMEM_GET_PIN_STATUS) == ASHMEM_IS_PINNED, "ASHMEM_GET_PIN_STATUS = pinned");
	{
		int plain = open("/proc/self/exe", O_RDONLY);
		CHECK(plain >= 0 && ioctl(plain, ASHMEM_GET_SIZE) < 0, "ASHMEM_GET_SIZE refused on a non-memfd file");
	}
	printf(fails ? "ashmem_memfd_test FAILED (%d failures)\n" : "ashmem_memfd_test PASSED (0 failures)\n", fails);
	return fails ? 1 : 0;
}
