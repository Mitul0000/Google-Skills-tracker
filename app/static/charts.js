/**
 * Lightweight SVG Chart generator (no external libraries)
 */
const Charts = {
  // Palette of distinguishable colors for score slices 10 down to 0, private, and errors
  colors: {
    10: "#1e8e3e", // Dark Green (Complete)
    9: "#34a853",
    8: "#46bd61",
    7: "#70c984",
    6: "#9ad9a6",
    5: "#fdd663", // Yellowish / Mid
    4: "#f9ab00",
    3: "#fa7b17",
    2: "#e37400",
    1: "#ea4335",
    0: "#c5221f", // Red
    private: "#9334e6", // Purple
    errors: "#d93025",  // Crimson Red
  },

  /**
   * Draws an SVG Pie/Donut Chart.
   * slices: [{ label, value, color, key }]
   * options: { size, innerRadius }
   */
  createPieChart(slices, options = {}) {
    const size = options.size || 260;
    const innerRadius = options.innerRadius || 0;
    const radius = size / 2;
    const total = slices.reduce((acc, s) => acc + s.value, 0);

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", size);
    svg.setAttribute("height", size);
    svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
    svg.style.borderRadius = "50%";

    if (total === 0) {
      // Empty circle
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", radius);
      circle.setAttribute("cy", radius);
      circle.setAttribute("r", radius - 2);
      circle.setAttribute("fill", "#e0e0e0");
      svg.appendChild(circle);
      return svg;
    }

    let startAngle = 0;

    slices.forEach((slice) => {
      if (slice.value <= 0) return;
      const angle = (slice.value / total) * 360;
      const endAngle = startAngle + angle;

      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      const d = Charts.describeArc(radius, radius, radius, innerRadius, startAngle, endAngle);
      path.setAttribute("d", d);
      path.setAttribute("fill", slice.color);
      path.setAttribute("data-key", slice.key);
      path.style.transition = "transform 0.2s, opacity 0.2s";
      path.style.cursor = "pointer";

      // Tooltip
      const percent = ((slice.value / total) * 100).toFixed(1);
      const titleEl = document.createElementNS("http://www.w3.org/2000/svg", "title");
      titleEl.textContent = `${slice.label}: ${slice.value} (${percent}%)`;
      path.appendChild(titleEl);

      // Hover effect
      path.addEventListener("mouseenter", () => {
        path.style.opacity = "0.8";
      });
      path.addEventListener("mouseleave", () => {
        path.style.opacity = "1";
      });

      // Click to scroll to section
      path.addEventListener("click", () => {
        if (options.onClick) {
          options.onClick(slice.key);
        }
      });

      svg.appendChild(path);
      startAngle = endAngle;
    });

    return svg;
  },

  polarToCartesian(centerX, centerY, radius, angleInDegrees) {
    const angleInRadians = ((angleInDegrees - 90) * Math.PI) / 180.0;
    return {
      x: centerX + radius * Math.cos(angleInRadians),
      y: centerY + radius * Math.sin(angleInRadians),
    };
  },

  describeArc(x, y, outerR, innerR, startAngle, endAngle) {
    // If arc is virtually a full circle, slight offset to render correctly in SVG
    if (endAngle - startAngle >= 359.99) {
      endAngle = startAngle + 359.99;
    }

    const startOuter = Charts.polarToCartesian(x, y, outerR, endAngle);
    const endOuter = Charts.polarToCartesian(x, y, outerR, startAngle);
    const largeArcFlag = endAngle - startAngle <= 180 ? "0" : "1";

    if (innerR <= 0) {
      return [
        "M", x, y,
        "L", startOuter.x, startOuter.y,
        "A", outerR, outerR, 0, largeArcFlag, 0, endOuter.x, endOuter.y,
        "Z"
      ].join(" ");
    }

    const startInner = Charts.polarToCartesian(x, y, innerR, endAngle);
    const endInner = Charts.polarToCartesian(x, y, innerR, startAngle);

    return [
      "M", startOuter.x, startOuter.y,
      "A", outerR, outerR, 0, largeArcFlag, 0, endOuter.x, endOuter.y,
      "L", endInner.x, endInner.y,
      "A", innerR, innerR, 0, largeArcFlag, 1, startInner.x, startInner.y,
      "Z"
    ].join(" ");
  }
};
