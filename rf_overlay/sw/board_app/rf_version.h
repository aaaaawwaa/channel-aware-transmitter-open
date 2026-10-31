/* rf_version.h -- one version source for the RF daemon and protocol. */
#ifndef RF_VERSION_H_
#define RF_VERSION_H_

#define RF_DAEMON_VERSION "1.0.4"
#define RF_PROTOCOL_MAJOR 1u
/* Minor 1: device.info grew required response fields (bit_sha256, hwh_sha256,
 * capabilities).  Additive, so major is unchanged -- and the release manifest
 * records the major, not the minor, so this does not invalidate it. */
/* Minor 2: the error vocabulary grew `PERMISSION` (a rejected control token used to
 * borrow INVALID_STATE) and device.info already reports `armed` plus a separate
 * `capabilities.control_ops`.  All of it is ADDITIVE -- a phase-1 client ignores a
 * code it never triggers and fields it does not read -- and the release manifest
 * records the MAJOR, not the minor, so no published manifest is invalidated. */
/* Minor 3: explicit guarded raw RX1 observation through device.info.
 * Default requests add no gain reads and no operation is added. */
/* Minor 4: opt-in physical capture API timing and read-only trace retrieval.
 * IQ frames and the manifest's protocol major remain unchanged. */
/* Minor 5: bounded opt-in settling wait on physical capture.  ADDITIVE: a request
 * that omits it performs no sleep and no extra clock read, so the default
 * transaction is byte-identical to minor 4 -- and the release manifest records
 * the MAJOR, not the minor, so no published manifest is invalidated. */
/* Minor6: independent bounded HEADROOM-1 research operations. */
#define RF_PROTOCOL_MINOR 6u

#endif /* RF_VERSION_H_ */
