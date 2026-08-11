import { ImageResponse } from "next/og";

export const alt = "Scribe, evidence-first document intelligence";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "#f4f1eb",
          color: "#2a2826",
          padding: "70px 76px",
          fontFamily: "Arial, sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          <div
            style={{
              width: 54,
              height: 54,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "#2a2826",
              color: "#f4f1eb",
              borderRadius: 6,
              fontSize: 30,
            }}
          >
            S
          </div>
          <div style={{ display: "flex", fontSize: 34, fontWeight: 700 }}>Scribe</div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", maxWidth: 980 }}>
          <div style={{ display: "flex", fontSize: 74, lineHeight: 1.04, letterSpacing: -3, fontWeight: 700 }}>
            Ask your documents. Check every answer.
          </div>
          <div style={{ display: "flex", marginTop: 30, fontSize: 27, lineHeight: 1.4, color: "#55584a" }}>
            Private document search with inspectable retrieval, grounded responses, and source citations.
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, color: "#087565" }}>
            <div style={{ width: 11, height: 11, borderRadius: 999, background: "#087565" }} />
            Evidence-first document intelligence
          </div>
          <div style={{ display: "flex", color: "#6b705c" }}>Built by Santosh Kumar</div>
        </div>
      </div>
    ),
    size,
  );
}
