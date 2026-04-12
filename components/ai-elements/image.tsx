import type { Experimental_GeneratedImage } from "ai";

import { cn } from "@/lib/utils";

export type ImageProps = Experimental_GeneratedImage & {
  className?: string;
  alt?: string;
};

export const Image = ({
  base64,
  uint8Array: _uint8Array,
  mediaType,
  alt,
  className,
}: ImageProps) => (
  <img
    alt={alt}
    className={cn(
      "h-auto max-w-full overflow-hidden rounded-md",
      className,
    )}
    src={`data:${mediaType};base64,${base64}`}
  />
);
