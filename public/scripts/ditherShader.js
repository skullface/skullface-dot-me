class DitherShader {
  constructor() {
    this.canvas = document.createElement("canvas")
    this.gl = this.canvas.getContext("webgl") || this.canvas.getContext("experimental-webgl")

    if (!this.gl) {
      throw new Error("WebGL not supported")
    }

    this.program = null
    this.initialized = false
  }

  init() {
    if (this.initialized) return

    const vertexShaderSource = `
      attribute vec2 a_position;
      attribute vec2 a_texCoord;
      varying vec2 v_texCoord;
      
      void main() {
        gl_Position = vec4(a_position, 0.0, 1.0);
        v_texCoord = a_texCoord;
      }
    `

    const fragmentShaderSource = `
      precision mediump float;
      uniform sampler2D u_texture;
      uniform vec2 u_resolution;
      uniform float u_ditherStrength;
      uniform float u_pixelSize;
      varying vec2 v_texCoord;
      
      float getBayerValue(vec2 coord) {
        vec2 pos = floor(coord);
        float x = pos.x;
        float y = pos.y;
        
        // 4x4 Bayer matrix values
        if (x < 1.0) {
          if (y < 1.0) return 0.0/16.0;
          else if (y < 2.0) return 8.0/16.0;
          else if (y < 3.0) return 2.0/16.0;
          else return 10.0/16.0;
        } else if (x < 2.0) {
          if (y < 1.0) return 12.0/16.0;
          else if (y < 2.0) return 4.0/16.0;
          else if (y < 3.0) return 14.0/16.0;
          else return 6.0/16.0;
        } else if (x < 3.0) {
          if (y < 1.0) return 3.0/16.0;
          else if (y < 2.0) return 11.0/16.0;
          else if (y < 3.0) return 1.0/16.0;
          else return 9.0/16.0;
        } else {
          if (y < 1.0) return 15.0/16.0;
          else if (y < 2.0) return 7.0/16.0;
          else if (y < 3.0) return 13.0/16.0;
          else return 5.0/16.0;
        }
      }
      
      void main() {
        vec2 pixelatedCoord = floor(v_texCoord * u_resolution / u_pixelSize) * u_pixelSize / u_resolution;
        vec4 color = texture2D(u_texture, pixelatedCoord);
        
        vec2 ditherCoord = mod(gl_FragCoord.xy, 4.0);
        float bayerValue = getBayerValue(ditherCoord);
        
        // Convert to grayscale first for better dithering
        float gray = dot(color.rgb, vec3(0.299, 0.587, 0.114));
        
        // Apply dithering with stronger effect
        float dithered = gray + (bayerValue - 0.5) * u_ditherStrength * 0.3;
        
        // Quantize to fewer levels for more pronounced effect
        dithered = floor(dithered * 4.0) / 4.0;
        
        // Keep it grayscale
        vec3 result = vec3(dithered);
        
        gl_FragColor = vec4(result, color.a);
      }
    `

    // Create and compile shaders
    const vertexShader = this.createShader(this.gl.VERTEX_SHADER, vertexShaderSource)
    const fragmentShader = this.createShader(this.gl.FRAGMENT_SHADER, fragmentShaderSource)

    // Create program
    this.program = this.gl.createProgram()
    this.gl.attachShader(this.program, vertexShader)
    this.gl.attachShader(this.program, fragmentShader)
    this.gl.linkProgram(this.program)

    if (!this.gl.getProgramParameter(this.program, this.gl.LINK_STATUS)) {
      throw new Error(`Program link error: ${this.gl.getProgramInfoLog(this.program)}`)
    }

    // Set up geometry
    this.setupGeometry()
    this.initialized = true
  }

  createShader(type, source) {
    const shader = this.gl.createShader(type)
    this.gl.shaderSource(shader, source)
    this.gl.compileShader(shader)

    if (!this.gl.getShaderParameter(shader, this.gl.COMPILE_STATUS)) {
      const error = this.gl.getShaderInfoLog(shader)
      this.gl.deleteShader(shader)
      throw new Error(`Shader compile error: ${error}`)
    }

    return shader
  }

  setupGeometry() {
    // Create buffer for quad vertices with corrected texture coordinates
    // Position: x, y, Texture: u, v (flip v coordinate to fix upside down)
    const positions = new Float32Array([
      -1, -1, 0, 1,  // bottom-left
       1, -1, 1, 1,  // bottom-right  
      -1,  1, 0, 0,  // top-left
       1,  1, 1, 0   // top-right
    ])

    const buffer = this.gl.createBuffer()
    this.gl.bindBuffer(this.gl.ARRAY_BUFFER, buffer)
    this.gl.bufferData(this.gl.ARRAY_BUFFER, positions, this.gl.STATIC_DRAW)

    const positionLocation = this.gl.getAttribLocation(this.program, "a_position")
    const texCoordLocation = this.gl.getAttribLocation(this.program, "a_texCoord")

    this.gl.enableVertexAttribArray(positionLocation)
    this.gl.vertexAttribPointer(positionLocation, 2, this.gl.FLOAT, false, 16, 0)

    this.gl.enableVertexAttribArray(texCoordLocation)
    this.gl.vertexAttribPointer(texCoordLocation, 2, this.gl.FLOAT, false, 16, 8)
  }

  processImage(image, ditherStrength = 1.0, pixelSize = 1) {
    this.init()

    // Set canvas size to match image
    this.canvas.width = image.width
    this.canvas.height = image.height
    this.gl.viewport(0, 0, image.width, image.height)

    // Create texture from image
    const texture = this.gl.createTexture()
    this.gl.bindTexture(this.gl.TEXTURE_2D, texture)
    this.gl.texImage2D(this.gl.TEXTURE_2D, 0, this.gl.RGBA, this.gl.RGBA, this.gl.UNSIGNED_BYTE, image)
    this.gl.texParameteri(this.gl.TEXTURE_2D, this.gl.TEXTURE_WRAP_S, this.gl.CLAMP_TO_EDGE)
    this.gl.texParameteri(this.gl.TEXTURE_2D, this.gl.TEXTURE_WRAP_T, this.gl.CLAMP_TO_EDGE)
    this.gl.texParameteri(this.gl.TEXTURE_2D, this.gl.TEXTURE_MIN_FILTER, this.gl.NEAREST)
    this.gl.texParameteri(this.gl.TEXTURE_2D, this.gl.TEXTURE_MAG_FILTER, this.gl.NEAREST)

    // Use shader program
    this.gl.useProgram(this.program)

    // Set uniforms
    const resolutionLocation = this.gl.getUniformLocation(this.program, "u_resolution")
    const ditherStrengthLocation = this.gl.getUniformLocation(this.program, "u_ditherStrength")
    const pixelSizeLocation = this.gl.getUniformLocation(this.program, "u_pixelSize")
    const textureLocation = this.gl.getUniformLocation(this.program, "u_texture")

    this.gl.uniform2f(resolutionLocation, image.width, image.height)
    this.gl.uniform1f(ditherStrengthLocation, ditherStrength)
    this.gl.uniform1f(pixelSizeLocation, pixelSize)
    this.gl.uniform1i(textureLocation, 0)

    // Render
    this.gl.drawArrays(this.gl.TRIANGLE_STRIP, 0, 4)

    // Return processed image as canvas
    return this.canvas
  }

  // Convenience method to process an image and return as data URL
  processImageToDataURL(image, ditherStrength = 1.0, pixelSize = 1, format = "image/png") {
    const canvas = this.processImage(image, ditherStrength, pixelSize)
    return canvas.toDataURL(format)
  }

  // Convenience method to process an image and return as blob
  async processImageToBlob(image, ditherStrength = 1.0, pixelSize = 1, format = "image/png", quality = 0.92) {
    const canvas = this.processImage(image, ditherStrength, pixelSize)
    return new Promise((resolve) => {
      canvas.toBlob(resolve, format, quality)
    })
  }
}

// Export for use in modules
if (typeof module !== "undefined" && module.exports) {
  module.exports = DitherShader
}

// Make available globally for use in Astro components
if (typeof window !== "undefined") {
  window.DitherShader = DitherShader
} 