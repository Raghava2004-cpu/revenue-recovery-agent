/*
  Inline SVG icon set — no icon library, no font, no network request.

  All icons are 24×24 on a common grid, 1.6 stroke, `currentColor`, so they
  inherit text colour and sit optically level with the type beside them. Size is
  set once by the wrapper rather than per-path, which is what keeps them from
  drifting out of alignment as the layout changes.
*/
function Svg({ size = 18, children, ...rest }) {
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" focusable="false" {...rest}
    >
      {children}
    </svg>
  );
}

/* ---- pipeline stages ---- */

export const IconDetect = (p) => (
  <Svg {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="M11 7v4l2.5 2.5M20 20l-3.6-3.6" />
  </Svg>
);

export const IconDiagnose = (p) => (
  <Svg {...p}>
    <path d="M3 5h3l2.5 12L11 9l2 5 1.6-3H21" />
  </Svg>
);

export const IconDecide = (p) => (
  <Svg {...p}>
    <path d="M12 4v16M7 8H4l3 6 3-6H7zM17 8h-3l3 6 3-6h-3zM6 20h12" />
  </Svg>
);

export const IconAct = (p) => (
  <Svg {...p}>
    <path d="M21 3 10.5 13.5M21 3l-6.8 18-3.7-7.5L3 9.8 21 3z" />
  </Svg>
);

export const IconObserve = (p) => (
  <Svg {...p}>
    <path d="M20 6 9 17l-5-5" />
  </Svg>
);

/* ---- panels ---- */

export const IconShield = (p) => (
  <Svg {...p}>
    <path d="M12 3l7 3v5.5c0 4.3-2.9 8.2-7 9.5-4.1-1.3-7-5.2-7-9.5V6l7-3z" />
    <path d="M9.5 12l1.8 1.8L15 10" />
  </Svg>
);

export const IconUsers = (p) => (
  <Svg {...p}>
    <circle cx="9" cy="8" r="3.2" />
    <path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5M16 5.2a3.2 3.2 0 010 5.9M18 20c0-2.4-.9-4-2-4.9" />
  </Svg>
);

export const IconChart = (p) => (
  <Svg {...p}>
    <path d="M4 20V4M4 20h16M8 16v-4M12 16V8M16 16v-6" />
  </Svg>
);

export const IconScale = (p) => (
  <Svg {...p}>
    <path d="M4 7h16M9 7l-4 7h8L9 7zM19 7l-4 7" opacity=".0" />
    <path d="M12 5v14M6 19h12M5 9l-2.5 5h5L5 9zM19 9l-2.5 5h5L19 9zM5 9l7-2 7 2" />
  </Svg>
);

export const IconTable = (p) => (
  <Svg {...p}>
    <rect x="3" y="4.5" width="18" height="15" rx="2" />
    <path d="M3 9.5h18M9 9.5v10" />
  </Svg>
);

export const IconRupee = (p) => (
  <Svg {...p}>
    <path d="M7 4h10M7 8.5h10M16 4c0 3.6-2.6 4.5-5.5 4.5H7l8 11" />
  </Svg>
);

export const IconAlert = (p) => (
  <Svg {...p}>
    <path d="M12 4.5 2.8 20h18.4L12 4.5zM12 10v4M12 17h.01" />
  </Svg>
);

export const IconLock = (p) => (
  <Svg {...p}>
    <rect x="4.5" y="10.5" width="15" height="10" rx="2" />
    <path d="M8 10.5V7.8a4 4 0 018 0v2.7" />
  </Svg>
);

export const IconArrow = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
       strokeLinejoin="round" aria-hidden="true">
    <path d="M5 12h14M13 6l6 6-6 6" />
  </svg>
);
