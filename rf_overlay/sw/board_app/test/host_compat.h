#ifndef RF_HOST_COMPAT_H_
#define RF_HOST_COMPAT_H_
#include <string.h>
static char *rf_host_strsep(char **stringp, const char *delim)
{
    char *start;
    char *end;
    if (!stringp || !*stringp)
        return NULL;
    start = *stringp;
    end = strpbrk(start, delim);
    if (end) {
        *end = '\0';
        *stringp = end + 1;
    } else {
        *stringp = NULL;
    }
    return start;
}
#define strsep rf_host_strsep
#endif
