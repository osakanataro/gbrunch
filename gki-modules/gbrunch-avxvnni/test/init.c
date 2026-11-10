// SPDX-License-Identifier: GPL-2.0
/* PID 1 of the QEMU test initramfs: load the module, run /vnni_test, power off. */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

int main(void)
{
	int fd = open("/dev/ttyS0", O_RDWR), st = -1;

	/* The GKI built-in cmdline ends with console=ttynull, so /dev/console is silent. */
	if (fd >= 0) { dup2(fd, 0); dup2(fd, 1); dup2(fd, 2); }
	setvbuf(stdout, NULL, _IONBF, 0);
	fd = open("/gbrunch_avxvnni.ko", O_RDONLY);

	if (fd < 0 || syscall(SYS_finit_module, fd, "", 0))
		printf("init: module load failed: %s\n", strerror(errno));
	else
		printf("init: module loaded\n");
	if (fork() == 0) {
		execl("/vnni_test", "/vnni_test", NULL);
		_exit(127);
	}
	wait(&st);
	printf("init: vnni_test exit status %d%s\n", WEXITSTATUS(st), WIFSIGNALED(st) ? " (killed by signal)" : "");
	mount("sysfs", "/sys", "sysfs", 0, NULL);
	fd = open("/sys/module/gbrunch_avxvnni/parameters/emulated", O_RDONLY);
	if (fd >= 0) {
		char b[32] = ""; int n = read(fd, b, 31); if (n > 0) b[n] = 0;
		printf("init: emulated=%s", b);
	}
	fd = open("/sys/module/gbrunch_avxvnni/parameters/declined", O_RDONLY);
	if (fd >= 0) {
		char b[32] = ""; int n = read(fd, b, 31); if (n > 0) b[n] = 0;
		printf("init: declined=%s", b);
	}
	sync();
	reboot(RB_POWER_OFF);
	return 0;
}
