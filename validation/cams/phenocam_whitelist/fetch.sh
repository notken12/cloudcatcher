#!/bin/sh
s=$1; [ -f "midday/$s.jpg" ] && exit 0
u=$(curl -s -m 30 "https://phenocam.nau.edu/api/middayimages/$s/" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[-1]['imgpath'].replace('_IR_','_'))" 2>/dev/null)
[ -n "$u" ] && curl -s -m 30 "https://phenocam.nau.edu$u" -o "midday/$s.jpg" -w "$s %{http_code} $u\n"
