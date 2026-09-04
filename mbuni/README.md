# Public Mbuni build

Source: [fredounnet/mbuni tag 1.6.0](https://github.com/fredounnet/mbuni/tree/1.6.0), commit `b8054f9ddfc48a8f2ec911adabd5309472bcf9f4`.

Archive SHA-256: `af646c1aba7d1eb62b0681e6ee3a931f888ddf7fdd779385b38c3c976e390223`.

The Dockerfile uses Debian Bookworm's Kannel 1.4.5 development package and applies two source compatibility changes:

- Rename the `bool` parameter in `mmlib/mms_cfg.c`, which conflicts with modern headers.
- Use Kannel's current `conn_use_global_*` names for the three TLS configuration functions in `mmlib/mms_util.c`.

Mbuni's libraries are linked statically because Debian's Kannel archives are not position-independent. The upstream PostgreSQL queue module is compiled separately as a shared module and resolves Mbuni/Kannel symbols from the executable's exported symbols. The legacy OpenSSL configure probe is disabled; Kannel supplies its TLS implementation. The lab tests HTTP egress, not TLS interoperability.

There are no changes to upstream SOAP construction, routing, delivery, retry timing, or terminal-error behavior. PostgreSQL is configured per Mbuni connection with `standard_conforming_strings=off`, matching the old module's `PQescapeByteaConn` plus `E'...'` SQL encoding. Other clients retain PostgreSQL defaults. The queue uses internal bytea message storage and native archive tables.

`tables.sql` is copied from the same upstream source. The image includes the patched source and upstream license files in `/opt/mbuni/share/source`. The source is GPL with upstream exceptions; retain the supplied licenses when redistributing. This older public build is for a demo and does not establish compatibility with Skycore's private implementation.

The lab's `init_db()` adds `mms_message_headers_qid_idx` on the active header table's `qid` column, including on existing databases. The upstream SQL file remains unchanged. Native header reads, rewrites, archiving, and cascading queue deletion all use this key; indexing it avoids repeated full-table scans as the backlog grows. See [database CPU and upgrade instructions](../README.md#postgresql-cpu-during-large-queue-tests).

The image uses the target architecture's Debian and Kannel packages. Before configuring Mbuni, it replaces upstream's 2006 `autotools/config.guess` and `config.sub` with Debian's maintained copies (installed by the `automake` dependency). The old `config.guess` fails on `aarch64` with `unable to guess system type`. This updates build-time platform detection without changing Mbuni's sending or retry behavior. The replacement helpers are also included in the patched source archive inside the image.

See [Apple Silicon startup and rebuild instructions](../README.md#apple-silicon-macs). Prefer a native ARM64 image on Apple Silicon for throughput demos; running an AMD64 image through emulation adds overhead.

Validation: the image builds for both `linux/amd64` and `linux/arm64`. The existing integration suite passed with the ARM64 Mbuni image running under emulation on an AMD64 Linux host (11 passed; two optional logging tests skipped), including native PostgreSQL queueing, bundled image submission, HTTP 429/503 retries, recovery, and retry exhaustion. The surrounding test services ran as AMD64. Docker Desktop on physical Apple Silicon has not been tested here.
