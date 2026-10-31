/* strsep_shim.h -- supply the declaration MinGW's <string.h> does not have.
 *
 * This file is NOT part of the vendor tree and vendor/ is NOT patched.  A
 * verbatim copy of the AD9361 no-OS driver lives under vendor/ and is
 * byte-checked against vendor/SHA256SUMS; nothing here changes it.  What
 * happens instead is that host_probe.sh passes this header to the compiler with
 * -include, so vendor/ad9361.c sees the prototype it needs without an edit to
 * the file itself.
 *
 * strsep is a GNU extension.  It is an ordinary libc function, so the only
 * thing missing on MinGW is the declaration and the definition -- see
 * strsep_shim.c.  If the host libc ever grows a real strsep, this becomes dead
 * weight and the two files can go.
 */
#ifndef BOARD_APP_TEST_STRSEP_SHIM_H
#define BOARD_APP_TEST_STRSEP_SHIM_H

char *strsep(char **sp, const char *delim);

#endif
