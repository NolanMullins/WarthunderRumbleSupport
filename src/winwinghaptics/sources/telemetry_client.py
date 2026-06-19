"""War Thunder localhost telemetry client.

Thin stdlib HTTP client over the game's local web server (localhost:8111). Reused keep-alive
connection; any error drops the connection and returns None so the caller simply retries.

Endpoints used:
  /indicators                       -> cockpit indicators; weapon2==1.0 means the gun trigger
                                       is held (lowest-latency gun signal).
  /hudmsg?lastEvt=&lastDmg=          -> kill / damage text feed (callsign-matched outcomes).
"""
import json
import http.client


class WarThunder:
    def __init__(self, host="localhost", port=8111, timeout=0.5):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.conn = None

    def _get(self, path):
        try:
            if self.conn is None:
                self.conn = http.client.HTTPConnection(self.host, self.port,
                                                       timeout=self.timeout)
            self.conn.request("GET", path, headers={"Connection": "keep-alive"})
            r = self.conn.getresponse()
            body = r.read()
            if r.status != 200:
                return None
            return json.loads(body.decode("utf-8", "replace"))
        except Exception:
            try:
                if self.conn:
                    self.conn.close()
            except Exception:
                pass
            self.conn = None
            return None

    def indicators(self):
        return self._get("/indicators")

    def hudmsg(self, last_evt, last_dmg):
        return self._get(f"/hudmsg?lastEvt={last_evt}&lastDmg={last_dmg}")
