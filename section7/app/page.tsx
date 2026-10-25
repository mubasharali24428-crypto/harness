"use client";

// "/" is two surfaces: signed-out visitors get the landing page, a session
// gets the Command Center. The server render has no localStorage, so both
// sides start on the landing and the swap happens after mount — no hydration
// mismatch. The dashboard itself lives in components/CommandCenter.tsx.

import { useEffect, useState } from "react";
import * as React from "react";
import { CommandCenterView } from "@/components/CommandCenter";
import { LandingView } from "@/components/Landing";
import { getToken } from "@/lib/auth";

export default function CommandCenter() {
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    if (getToken()) setSignedIn(true);
  }, []);

  return signedIn ? <CommandCenterView /> : <LandingView />;
}
