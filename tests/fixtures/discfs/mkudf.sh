#!/bin/sh
# Run as root on a machine with the udf kernel module. Builds populated UDF images in $PWD/out.
set -eu
W=$(pwd); mkdir -p out mnt
mk() { # name size media udfrev [frag]
  name=$1; size=$2; media=$3; rev=$4; frag=${5:-}
  img=out/$name.img; rm -f $img; truncate -s $size $img
  if ! mkudffs --media-type=$media --udfrev=$rev --blocksize=2048 --label=$name $img >/dev/null 2>&1; then echo "$name: mkudffs refused"; rm -f $img; return; fi
  if ! mount -o loop,rw $img mnt 2>/dev/null; then echo "$name: cannot mount rw (left empty)"; return; fi
  if ! python3 mktree.py mnt 2>/dev/null; then umount mnt; echo "$name: mounted read-only (left empty)"; return; fi
  if [ -n "$frag" ]; then
    i=0; while head -c 16384 /dev/zero > mnt/fill_$i.bin 2>/dev/null && [ -s mnt/fill_$i.bin ]; do i=$((i+1)); done
    rm -f mnt/fill_$i.bin; sync
    j=0; while [ $j -lt $i ]; do rm -f mnt/fill_$j.bin; j=$((j+2)); done; sync
    python3 -c "
import sys; sys.path.insert(0,'$W'); from mktree import content
open('mnt/frag.wav','wb').write(content(600000))"
    sync; rm -f mnt/fill_*.bin; ls mnt | grep -c fill_ || true
  fi
  sync; umount mnt
  echo "$name: ok"
}
mk udf102_hd       6M  hd     0x0102 frag
mk udf102_dvd      6M  dvd    0x0102
mk udf150_dvdrw    8M  dvdrw  0x0150 frag
mk udf201_dvdrw    8M  dvdrw  0x0201 frag
mk udf201_dvdram   6M  dvdram 0x0201
mk udf250_bdr      8M  bdr    0x0250
mk udf250_bd       10M bd     0x0250 frag
mk udf260_bd       10M bd     0x0260
mk udf150_cdr_vat  6M  cdr    0x0150
mk udf201_dvdr_vat 6M  dvdr   0x0201
rmdir mnt
