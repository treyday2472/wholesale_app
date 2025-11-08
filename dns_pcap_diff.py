#!/usr/bin/env python3
"""
dns_pcap_diff.py
Compare DNS behavior between two packet captures (PCAP/PCAPNG).

It tries PyShark (tshark) first for rich DNS parsing. If PyShark/tshark
aren't available, it falls back to Scapy.

Outputs:
- Console summary of differences (queries, answers, rcodes, timeouts).
- CSV report: dns_diff_report.csv

Usage:
    python dns_pcap_diff.py --working <working.pcapng> --broken <broken.pcapng>

Optional:
    python dns_pcap_diff.py --working <working.pcapng> --broken <broken.pcapng> --filter "kphx6.sips.vonageservices.com"
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

def try_import_pyshark():
    try:
        import pyshark  # noqa: F401
        return True
    except Exception:
        return False

def try_import_scapy():
    try:
        from scapy.all import rdpcap, DNS, DNSRR  # noqa: F401
        return True
    except Exception:
        return False

def parse_with_pyshark(path):
    import pyshark
    cap = pyshark.FileCapture(path, display_filter="dns")
    results = []
    for pkt in cap:
        try:
            layer = pkt.dns
            entry = {
                "time": str(pkt.sniff_time),
                "src": getattr(pkt.ip, "src", None) if hasattr(pkt, "ip") else None,
                "dst": getattr(pkt.ip, "dst", None) if hasattr(pkt, "ip") else None,
                "query": getattr(layer, "qry_name", None),
                "rcode": getattr(layer, "flags_rcode", None),
                "answers": [],
                "is_response": getattr(layer, "flags_response", None) == "1",
            }
            # Collect A/AAAA answers
            # PyShark exposes dns.a (first A), dns.a_all (list)
            if hasattr(layer, "a_all"):
                for ip in layer.a_all:
                    entry["answers"].append(("A", ip))
            elif hasattr(layer, "a"):
                entry["answers"].append(("A", layer.a))
            if hasattr(layer, "aaaa_all"):
                for ip6 in layer.aaaa_all:
                    entry["answers"].append(("AAAA", ip6))
            elif hasattr(layer, "aaaa"):
                entry["answers"].append(("AAAA", layer.aaaa))
            results.append(entry)
        except Exception:
            continue
    cap.close()
    return results

def parse_with_scapy(path):
    from scapy.all import rdpcap, DNS, DNSRR
    pkts = rdpcap(path)
    results = []
    for p in pkts:
        if p.haslayer(DNS):
            dns = p[DNS]
            entry = {
                "time": getattr(p, "time", None),
                "src": p[0].src if hasattr(p[0], "src") else None,
                "dst": p[0].dst if hasattr(p[0], "dst") else None,
                "query": None,
                "rcode": dns.rcode if hasattr(dns, "rcode") else None,
                "answers": [],
                "is_response": dns.qr == 1
            }
            try:
                if dns.qd is not None:
                    entry["query"] = dns.qd.qname.decode(errors="ignore").rstrip(".")
            except Exception:
                pass
            try:
                if dns.an is not None and dns.ancount > 0:
                    for i in range(dns.ancount):
                        ans = dns.an[i]
                        if isinstance(ans, DNSRR):
                            rrname = ans.rrname.decode(errors="ignore").rstrip(".")
                            # ans.type 1=A, 28=AAAA
                            rrtype = "A" if getattr(ans, "type", 0) == 1 else ("AAAA" if getattr(ans, "type", 0) == 28 else str(getattr(ans, "type", "")))
                            rdata = getattr(ans, "rdata", "")
                            if isinstance(rdata, bytes):
                                try:
                                    rdata = rdata.decode()
                                except Exception:
                                    rdata = repr(rdata)
                            entry["answers"].append((rrtype, str(rdata)))
            except Exception:
                pass
            results.append(entry)
    return results

def load_dns_records(path):
    parsers = []
    if try_import_pyshark():
        parsers.append(("pyshark", parse_with_pyshark))
    if try_import_scapy():
        parsers.append(("scapy", parse_with_scapy))

    if not parsers:
        print("[!] Neither pyshark (tshark) nor scapy is available. Install one of them:")
        print("    pip install pyshark   # requires tshark installed")
        print("    OR")
        print("    pip install scapy")
        sys.exit(2)

    last_err = None
    for name, fn in parsers:
        try:
            return fn(path), name
        except Exception as e:
            last_err = e
            continue
    print(f"[!] Failed to parse {path}: {last_err}")
    sys.exit(2)

def summarize(records, fqdn_filter=None):
    """
    Build dictionaries:
      - queries -> counts
      - answers[query] -> set of (type, ip)
      - rcodes[query] -> set of rcode values
    """
    q_counts = defaultdict(int)
    ans = defaultdict(set)
    rcodes = defaultdict(set)

    for r in records:
        q = (r.get("query") or "").rstrip(".") if r.get("query") else None
        if fqdn_filter and q and fqdn_filter.lower() not in q.lower():
            continue

        if r.get("is_response"):
            # only aggregate responses into answers/rcodes
            if q:
                q_counts[q] += 1
                for rrtype, ip in r.get("answers", []):
                    ans[q].add((rrtype, ip))
                rcodes[q].add(str(r.get("rcode")))
        else:
            # count queries too (even if no response captured)
            if q:
                q_counts[q] += 0  # keep presence without inflating count

    return q_counts, ans, rcodes

def compare_summaries(sum_work, sum_broken):
    qW, aW, rW = sum_work
    qB, aB, rB = sum_broken

    all_queries = set(qW.keys()) | set(qB.keys())
    diff = []

    for q in sorted(all_queries):
        answersW = aW.get(q, set())
        answersB = aB.get(q, set())
        rcodesW = rW.get(q, set())
        rcodesB = rB.get(q, set())

        onlyW = answersW - answersB
        onlyB = answersB - answersW

        status = []
        if onlyW and not answersB:
            status.append("RESOLVES in working, NO ANSWER in broken")
        if onlyB and not answersW:
            status.append("RESOLVES in broken, NO ANSWER in working")
        if answersW and answersB and answersW != answersB:
            status.append("Different IP answers")
        if rcodesW != rcodesB:
            status.append(f"Rcode differs: working={','.join(rcodesW or {'NA'})} broken={','.join(rcodesB or {'NA'})}")

        if not status and (q not in aW and q in aB):
            status.append("Only present in broken capture")
        if not status and (q in aW and q not in aB):
            status.append("Only present in working capture")
        if not status:
            status.append("No difference detected")

        diff.append({
            "query": q,
            "working_ips": ";".join(sorted({ip for t, ip in answersW})) if answersW else "",
            "broken_ips": ";".join(sorted({ip for t, ip in answersB})) if answersB else "",
            "working_types": ";".join(sorted({t for t, ip in answersW})) if answersW else "",
            "broken_types": ";".join(sorted({t for t, ip in answersB})) if answersB else "",
            "working_rcodes": ";".join(sorted(rcodesW)) if rcodesW else "",
            "broken_rcodes": ";".join(sorted(rcodesB)) if rcodesB else "",
            "note": " | ".join(status)
        })
    return diff

def write_csv(rows, out_path="dns_diff_report.csv"):
    fields = ["query", "working_ips", "broken_ips", "working_types", "broken_types", "working_rcodes", "broken_rcodes", "note"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return os.path.abspath(out_path)

def main():
    ap = argparse.ArgumentParser(description="Diff DNS behavior between two PCAPs.")
    ap.add_argument("--working", required=True, help="Path to working PCAP/PCAPNG")
    ap.add_argument("--broken", required=True, help="Path to broken PCAP/PCAPNG")
    ap.add_argument("--filter", help="Substring to filter queries by (e.g., 'vonageservices.com')", default=None)
    args = ap.parse_args()

    print(f"[+] Parsing working capture: {args.working}")
    recW, parserW = load_dns_records(args.working)
    print(f"    Parsed with: {parserW}, records: {len(recW)}")

    print(f"[+] Parsing broken capture: {args.broken}")
    recB, parserB = load_dns_records(args.broken)
    print(f"    Parsed with: {parserB}, records: {len(recB)}")

    sumW = summarize(recW, fqdn_filter=args.filter)
    sumB = summarize(recB, fqdn_filter=args.filter)

    rows = compare_summaries(sumW, sumB)

    print("\n=== DNS Differences ===")
    any_diff = False
    for r in rows:
        if r["note"] != "No difference detected":
            any_diff = True
            print(f"- {r['query']}: {r['note']}")
            if r["working_ips"] or r["broken_ips"]:
                print(f"  working IPs: {r['working_ips'] or '—'}")
                print(f"  broken  IPs: {r['broken_ips'] or '—'}")

    if not any_diff:
        print("No material differences detected (at least at DNS resolution level).")

    out_csv = write_csv(rows)
    print(f"\n[+] CSV written: {out_csv}")

if __name__ == "__main__":
    main()
