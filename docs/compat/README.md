# Compatibility matrix fields

`matrix.yml` holds one record per tested pair: one host and one Mac. Add a record from a
[hardware report](../../.github/ISSUE_TEMPLATE/hardware-report.yml).

| Field | Meaning |
|---|---|
| `id` | Short unique name for the pair and month. |
| `host` | Generic host model, CPU architecture, GPU class and operating system. No serial numbers or hostnames. |
| `mac` | Generic Mac model and macOS version. |
| `versions` | Diarchy, Moonlight, Lan Mouse and Sunshine versions in the report. |
| `results` | `ok`, `partial`, `broken`, `untested` or `na` for each feature. |
| `evidence` | `M` maintainer verified, `C1` one community report, `C2+` two or more independent reports, `none`. |
| `report` | Link to the hardware report issue. Required for `ok` unless `evidence` is `M`. |
| `tested` | Year and month of the test. |

`untested` never means "works". A missing row means unknown.
