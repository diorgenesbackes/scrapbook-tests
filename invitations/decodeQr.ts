import type { Locator } from '@playwright/test'
import jsQR from 'jsqr'

export async function decodeQr(qr: Locator) {
  const pixels = await qr.evaluate(async element => {
    const svg = new XMLSerializer().serializeToString(element)
    const image = new Image()
    image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg)
    await image.decode()
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 384
    const context = canvas.getContext('2d')!
    context.imageSmoothingEnabled = false
    context.drawImage(image, 0, 0, 384, 384)
    return Array.from(context.getImageData(0, 0, 384, 384).data)
  })
  return jsQR(new Uint8ClampedArray(pixels), 384, 384)?.data
}
