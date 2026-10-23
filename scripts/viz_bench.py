#!/usr/bin/env python3
"""Helper for viz-bench.sh: CPU % from two /proc samples + draw stats from the perf log.

  viz_bench.py cpu  <pid> ...                 -> prints "<utime+stime seconds>" per pid
  viz_bench.py row  <tvapp> <perflog> <t0> <t1> <mode> <py_s> <x_s> <wall_s>
        t0/t1: perf-log clock seconds of the sample window; py_s/x_s: CPU seconds used in it.
"""
import os
import sys


def cpu_secs(pid):
    f = open("/proc/%s/stat" % pid).read().rsplit(")", 1)[1].split()
    return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")


def main(argv):
    if argv[0] == "cpu":
        print(" ".join("%.3f" % cpu_secs(p) for p in argv[1:]))
        return
    if argv[0] == "now":     # perf-log time of the last record (the log's clock is its own)
        import json
        last = 0.0
        try:
            for line in open(argv[1]):
                try:
                    last = max(last, json.loads(line).get("t", 0.0))
                except ValueError:
                    pass
        except OSError:
            pass
        print("%.3f" % last)
        return
    _, tvapp, log, t0, t1, mode, py_s, x_s, wall = argv
    sys.path.insert(0, tvapp)
    import perflog
    t0, t1, py_s, x_s, wall = float(t0), float(t1), float(py_s), float(x_s), float(wall)
    recs = [r for r in perflog.load(log) if r["ev"] != "cpu" and t0 <= r["t"] <= t1]
    draws = [r for r in recs if r["ev"] == "draw"]
    ms = sum(r["ms"] for r in draws) / len(draws) if draws else float("nan")
    area = sum(r["area"] for r in draws) / len(draws) if draws else float("nan")
    py, x = 100 * py_s / wall, 100 * x_s / wall
    print("%-14s | %8.1f | %7.1f | %7.1f | %7.2f | %6.1f | %5.1f" % (
        mode, py, x, py + x, ms, area, len(draws) / wall))


if __name__ == "__main__":
    main(sys.argv[1:])
