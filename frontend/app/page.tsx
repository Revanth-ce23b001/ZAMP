// Cover screen. The only dark surface in the product: everything behind the Work Queue stays on
// the light design tokens. Text here is white and white at reduced opacity, because the project's
// ink tokens are unreadable on this backdrop.

import Link from "next/link";

import { Hero } from "@/components/ui/tailwind-css-background-snippet";
import { APP_MARK, APP_NAME } from "@/lib/brand";

export default function Cover() {
  return (
    <Hero className="flex min-h-screen flex-col items-center justify-center px-6 py-20 text-center">
      <span aria-hidden
            className="flex h-12 w-12 items-center justify-center rounded-ctl bg-white/15 text-[15px] font-semibold tracking-tight text-white ring-1 ring-white/35">
        {APP_MARK}
      </span>

      <h1 className="mt-7 text-[40px] font-semibold leading-[1.1] tracking-tight text-white sm:text-[56px]">
        {APP_NAME}
      </h1>

      <p className="mt-4 max-w-xl text-[17px] leading-relaxed text-white/80 sm:text-[19px]">
        AI reads the invoice. Deterministic rules decide. A person resolves every exception.
      </p>

      <div className="mt-9 flex flex-col items-center gap-3 sm:flex-row">
        <Link href="/work-queue"
              className="inline-flex min-h-11 items-center justify-center rounded-ctl bg-white px-6 text-sm font-semibold text-ink no-underline transition-colors hover:bg-white/90">
          Open the work queue
        </Link>
        <Link href="/policy"
              className="inline-flex min-h-11 items-center justify-center rounded-ctl px-6 text-sm font-medium text-white/80 no-underline ring-1 ring-white/25 transition-colors hover:bg-white/10 hover:text-white">
          See the policy
        </Link>
      </div>

      <p className="mt-12 text-[12px] text-white/55">
        Prototype. All data is synthetic. Money is stored as integer paise.
      </p>
    </Hero>
  );
}
