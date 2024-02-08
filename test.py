from scapy.all import *
import sys

def custom_traceroute(dest_ip, dest_port, max_hops=30):
    ttl = 1
    timeout = 2

    while True:
        ip = IP(dst=dest_ip, ttl=ttl)
        tcp = TCP(dport=dest_port, flags='S')
        packet = ip/tcp

        reply = sr1(packet, verbose=0, timeout=timeout)

        if reply is None:
            print(f"{ttl}:\t No reply")
        elif reply.haslayer(TCP):
            if reply.getlayer(TCP).flags == 0x12:  # SYN-ACK flags
                print(f"{ttl}:\t {reply.src} (port is open)")
                break
            elif reply.getlayer(TCP).flags == 0x14:  # RST-ACK flags
                print(f"{ttl}:\t {reply.src} (port is closed)")
                break
        elif reply.haslayer(ICMP):
            print(f"{ttl}:\t {reply.src} (ICMP)")
            if int(reply.getlayer(ICMP).type) == 3 and int(reply.getlayer(ICMP).code) in [1, 2, 3, 9, 10, 13]:
                print("Blocked by a filtering device")
                break

        ttl += 1
        if ttl > max_hops:
            print("Max hops exceeded")
            break

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 script.py [destination IP] [destination Port]")
        sys.exit(1)

    destination_ip = sys.argv[1]
    destination_port = int(sys.argv[2])
    custom_traceroute(destination_ip, destination_port)