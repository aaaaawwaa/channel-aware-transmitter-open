/* strsep_shim.c -- a libc function, supplied from outside vendor/.
 *
 * See strsep_shim.h for why this exists and why it is not a vendor patch.  The
 * implementation is the standard one from the GNU libc manual: split at the
 * first delimiter, NUL it out, and advance.  Both call sites in the driver are
 * ordinary "parse a comma-separated list" loops that do not care about the
 * difference between strsep and strtok_r's NULL-before-a-gap behaviour.
 */
#include "strsep_shim.h"
#include <string.h>

char *strsep(char **sp, const char *delim)
{
	char *s = *sp, *p;

	if (s == NULL)
		return NULL;
	p = strpbrk(s, delim);
	if (p != NULL) {
		*p  = '\0';
		*sp = p + 1;
	} else {
		*sp = NULL;
	}
	return s;
}
