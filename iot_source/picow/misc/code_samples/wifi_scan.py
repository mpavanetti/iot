import network
import binascii
import rp2

rp2.country('US')

wlan = network.WLAN()
wlan.active(True)
networks = wlan.scan() # list with tuples (ssid, bssid, channel, RSSI, security, hidden)
networks.sort(key=lambda x:x[3],reverse=True) # sorted on RSSI (3)
for i, w in enumerate(networks):
      print(i+1, w[0].decode(), binascii.hexlify(w[1]).decode(), w[2], w[3], w[4], w[5])

