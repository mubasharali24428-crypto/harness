| Users | Journeys | Failures | Control p50/p95 (ms) | Engine-wait p50/p95/p99 (s) | Journey max (s) | Peak queued/running claims | Drain (s) | Audit (cumulative) |
|---:|---:|---:|---|---|---:|---|---:|---|
| 4 | 293 | 0 | 73/130 | 0.6/1.1/1.1 | 1.3 | 4/4 | 0 | runs=293 anomalies=none |
| 8 | 414 | 0 | 120/210 | 1.1/1.7/1.8 | 2.5 | 1/7 | 0 | runs=707 anomalies=none |
| 16 | 455 | 1 | 370/640 | 1.6/2.3/2.6 | 3.8 | 6/8 | 0 | runs=1163 anomalies={'lost': 1} |
| 32 | 450 | 0 | 490/750 | 3.7/5.7/6.0 | 7.2 | 22/6 | 0 | runs=1613 anomalies={'lost': 1} |
| 64 | 397 | 0 | 640/940 | 10.0/13.0/13.0 | 14.0 | 53/8 | 78 | runs=2010 anomalies={'lost': 1} |
| 100 | 347 | 0 | 820/1500 | 20.0/24.0/25.0 | 37.0 | 91/9 | 53 | runs=2357 anomalies={'lost': 1} |
