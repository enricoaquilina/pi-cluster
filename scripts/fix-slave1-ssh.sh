#!/bin/bash
set -uo pipefail
# Fix slave1 SSH after botched UID change
# Usage: 
#   1. Power off slave1
#   2. Remove its microSD card and plug into master (USB adapter)
#   3. Run: sudo bash /opt/workspace/scripts/fix-slave1-ssh.sh
#   4. Put card back and boot slave1

set -e

# Find slave1's root partition (most likely the last-inserted SD card)
echo "Looking for slave1's root partition..."
ROOTDEV=""
for dev in /dev/sd{c,d,e,f}2 /dev/mmcblk{1,2}p2; do
    if [ -b "$dev" ]; then
        echo "  Found: $dev"
        ROOTDEV="$dev"
        break
    fi
done

if [ -z "$ROOTDEV" ]; then
    echo "ERROR: No removable SD card partition found."
    echo "Available block devices:"
    lsblk
    echo ""
    echo "Set ROOTDEV manually: ROOTDEV=/dev/sdX2 $0"
    exit 1
fi

# Allow manual override
ROOTDEV="${ROOTDEV:-$1}"

MNTDIR=$(mktemp -d /tmp/slave1-root.XXXX)
echo "Mounting $ROOTDEV at $MNTDIR..."
mount "$ROOTDEV" "$MNTDIR"

if [ ! -d "$MNTDIR/home/enrico/.ssh" ]; then
    echo "ERROR: /home/enrico/.ssh not found on mounted filesystem"
    umount "$MNTDIR"
    rmdir "$MNTDIR"
    exit 1
fi

echo "Current state:"
ls -la "$MNTDIR/home/enrico/.ssh/"
echo ""

# Check current enrico UID in passwd
ENRICO_UID=$(grep "^enrico:" "$MNTDIR/etc/passwd" | cut -d: -f3)
ENRICO_GID=$(grep "^enrico:" "$MNTDIR/etc/passwd" | cut -d: -f4)
echo "enrico on slave1: UID=$ENRICO_UID GID=$ENRICO_GID"

# Fix 1: Complete the UID change (1001 -> 1000) in passwd/shadow/group
if [ "$ENRICO_UID" = "1001" ]; then
    echo "Fixing UID: 1001 -> 1000 in /etc/passwd"
    sed -i 's/^enrico:x:1001:/enrico:x:1000:/' "$MNTDIR/etc/passwd"
fi
if [ "$ENRICO_GID" != "1000" ]; then
    echo "Fixing GID in /etc/passwd"
    sed -i "s/^enrico:x:1000:${ENRICO_GID}:/enrico:x:1000:1000:/" "$MNTDIR/etc/passwd"
fi
# Fix group file too
if grep -q "^enrico:x:1001:" "$MNTDIR/etc/group"; then
    echo "Fixing enrico group: 1001 -> 1000"
    sed -i 's/^enrico:x:1001:/enrico:x:1000:/' "$MNTDIR/etc/group"
fi

# Fix 2: Chown all enrico files to 1000:1000
echo "Fixing ownership of /home/enrico/..."
chown -R 1000:1000 "$MNTDIR/home/enrico/"

# Fix 3: Fix SSH permissions
echo "Fixing .ssh permissions..."
chmod 700 "$MNTDIR/home/enrico/.ssh"
chmod 600 "$MNTDIR/home/enrico/.ssh/authorized_keys" 2>/dev/null || true
chmod 644 "$MNTDIR/home/enrico/.ssh/"*.pub 2>/dev/null || true

# Fix 4: Fix /tmp files
find "$MNTDIR/tmp" -user 1001 -exec chown 1000:1000 {} + 2>/dev/null || true

# Fix 5: Clean up the one-shot service we left behind
rm -f "$MNTDIR/etc/systemd/system/fix-uid.service"
rm -f "$MNTDIR/tmp/fix-uid.sh" "$MNTDIR/tmp/fix-uid.result"

echo ""
echo "Verification:"
grep "^enrico:" "$MNTDIR/etc/passwd"
grep "^dietpi:" "$MNTDIR/etc/passwd"
ls -la "$MNTDIR/home/enrico/.ssh/"

echo ""
echo "Unmounting..."
umount "$MNTDIR"
rmdir "$MNTDIR"
echo "Done! Put the SD card back in slave1 and boot it."
