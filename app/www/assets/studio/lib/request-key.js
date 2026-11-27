export function createRequestKey() {
  // 局域网 HTTP 直连也需要生成可用于幂等重试的 UUID v4。
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}
