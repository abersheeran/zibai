# Benchmark

All tests are conducted on my M1 Air using python3.11.

- `waitress`: `waitress-serve example:app`
- `gunicorn`: `gunicorn example:app`
- `gunicron-gevent`: `gunicorn example:app -k gevent`
- `zibai`: `python -m zibai example:app --no-gevent`
- `zibai-gevent`: `python -m zibai example:app`

## `waitress`

```bash
wrk -t 8 -c 40 -d 10 http://127.0.0.1:8080
Running 10s test @ http://127.0.0.1:8080
  8 threads and 40 connections
  Thread Stats   Avg      Stdev     Max   +/- Stdev
    Latency     2.56ms    2.10ms  81.23ms   97.78%
    Req/Sec     2.04k   345.45     2.52k    70.00%
  162591 requests in 10.01s, 22.79MB read
Requests/sec:  16245.71
Transfer/sec:      2.28MB
```

## `gunicorn`

```bash
wrk -t 8 -c 40 -d 10 http://127.0.0.1:8000
Running 10s test @ http://127.0.0.1:8000
  8 threads and 40 connections
  Thread Stats   Avg      Stdev     Max   +/- Stdev
    Latency    13.27ms    2.50ms  46.98ms   97.39%
    Req/Sec   370.89     47.08   404.00     89.55%
  16297 requests in 10.07s, 2.58MB read
  Socket errors: connect 1, read 0, write 0, timeout 0
Requests/sec:   1619.17
Transfer/sec:    262.48KB
```

## `gunicorn-gevent`

```bash
wrk -t 8 -c 40 -d 10 http://127.0.0.1:8000
Running 10s test @ http://127.0.0.1:8000
  8 threads and 40 connections
  Thread Stats   Avg      Stdev     Max   +/- Stdev
    Latency    69.41ms  259.15ms   1.97s    92.93%
    Req/Sec     1.38k     1.27k    3.26k    50.52%
  32226 requests in 10.08s, 5.26MB read
  Socket errors: connect 0, read 0, write 0, timeout 94
Requests/sec:   3196.38
Transfer/sec:    533.77KB
```

## `zibai`

```bash
wrk -t 8 -c 40 -d 10 http://127.0.0.1:9000
Running 10s test @ http://127.0.0.1:9000
  8 threads and 40 connections
  Thread Stats   Avg      Stdev     Max   +/- Stdev
    Latency   669.50us  781.06us  21.98ms   86.43%
    Req/Sec     5.03k     1.93k   10.21k    45.36%
  150994 requests in 10.10s, 12.96MB read
Requests/sec:  14950.50
Transfer/sec:      1.28MB
```

## `zibai-gevent`

```bash
wrk -t 8 -c 40 -d 10 http://127.0.0.1:9000
Running 10s test @ http://127.0.0.1:9000
  8 threads and 40 connections
  Thread Stats   Avg      Stdev     Max   +/- Stdev
    Latency   579.62us  315.64us  11.52ms   76.51%
    Req/Sec     4.35k     3.56k   12.09k    74.88%
  173980 requests in 10.10s, 14.93MB read
Requests/sec:  17226.59
Transfer/sec:      1.48MB
```
