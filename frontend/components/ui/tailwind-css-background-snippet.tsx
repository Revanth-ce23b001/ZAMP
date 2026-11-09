import { cn } from "@/lib/utils";

/**
 * Full-bleed radial-gradient backdrop: near-black at the centre top, violet at the edges.
 *
 * The gradient is the supplied snippet, unchanged. The only addition is `children`, so the
 * backdrop can carry content instead of rendering an empty box, and `className` on the content
 * layer. Used by the cover screen at `/`.
 *
 * Anything placed on this must be light: on the dark half of the gradient, the project's ink
 * tokens are unreadable. The cover screen uses white and white at reduced opacity.
 */
export const Hero = ({ children, className }: { children?: React.ReactNode; className?: string }) => {
  return (
    <div className={cn("w-full relative min-h-screen")}>
      {/* Background Pattern */}
      <div className="absolute inset-0">
        <div className="absolute inset-0 -z-10 h-full w-full items-center px-5 py-24 [background:radial-gradient(125%_125%_at_50%_10%,#000_40%,#63e_100%)]"></div>
      </div>

      {children && <div className={cn("relative z-10", className)}>{children}</div>}
    </div>
  );
};

export default Hero;
