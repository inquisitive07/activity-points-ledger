/** Design tokens from the VTU spec */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        maroon: '#7F1D1D',   // navbar / sidebar
        brand: '#B91C1C',    // primary CTA, active nav
        brandl: '#DC2626',   // hover, headings, error
        rosered: '#F43F5E',  // badges, chart accents
        soft: '#FDA4AF',     // row hover, secondary accents
        blush: '#FEF2F2',    // page background
        ink: '#1F2937',      // primary text
        mute: '#6B7280',     // secondary text
        ok: '#059669',
        warn: '#F59E0B',
        info: '#3B82F6',
      },
    },
  },
  plugins: [],
};
