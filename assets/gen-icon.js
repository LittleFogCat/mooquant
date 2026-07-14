const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let j = 0; j < 8; j++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length, 0);
  const tb = Buffer.from(type, 'ascii');
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(Buffer.concat([tb, data])), 0);
  return Buffer.concat([len, tb, data, crc]);
}
function makeIcon(size, r, g, b) {
  const sig = Buffer.from([0x89,0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a]);
  const ihd = Buffer.alloc(13);
  ihd.writeUInt32BE(size, 0); ihd.writeUInt32BE(size, 4);
  ihd[8]=8; ihd[9]=2; ihd[10]=0; ihd[11]=0; ihd[12]=0;
  const ihdr = chunk('IHDR', ihd);
  const raw = Buffer.alloc(size * (size * 3 + 1));
  const radius = size * 0.22;
  for (let y = 0; y < size; y++) {
    raw[y * (size * 3 + 1)] = 0;
    for (let x = 0; x < size; x++) {
      let inside = true;
      const cx = x < radius ? radius : (x >= size - radius ? size - radius - 1 : x);
      const cy = y < radius ? radius : (y >= size - radius ? size - radius - 1 : y);
      const dx = x - cx, dy = y - cy;
      if (dx*dx + dy*dy > radius*radius) inside = false;
      const o = y * (size * 3 + 1) + 1 + x * 3;
      if (inside) {
        const t = (x + y) / (2 * size);
        raw[o]   = Math.round(r * (1-t) + 13*t);
        raw[o+1] = Math.round(g * (1-t) + 110*t);
        raw[o+2] = Math.round(b * (1-t) + 253*t);
      } else { raw[o] = 0; raw[o+1] = 0; raw[o+2] = 0; }
    }
  }
  const idat = chunk('IDAT', zlib.deflateSync(raw));
  return Buffer.concat([sig, ihdr, idat, chunk('IEND', Buffer.alloc(0))]);
}

const outDir = 'D:/project/quant/mooquant/assets';
fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(path.join(outDir, 'icon.png'),     makeIcon(256, 0, 122, 255));
fs.writeFileSync(path.join(outDir, 'icon-512.png'), makeIcon(512, 0, 122, 255));
console.log('done:', fs.readdirSync(outDir).join(', '));