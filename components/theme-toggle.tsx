"use client";

import { useTheme } from "next-themes";
import { Sun, Moon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useEffect, useState } from "react";

export function ThemeToggle() {
    const { theme, setTheme, resolvedTheme } = useTheme();
    const [mounted, setMounted] = useState(false);

    useEffect(() => setMounted(true), []);

    if (!mounted) {
        return (
            <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 rounded-sm opacity-50"
            >
                <Sun className="h-3.5 w-3.5" />
            </Button>
        );
    }

    const isDark = resolvedTheme === "dark";

    return (
        <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 rounded-sm opacity-60 hover:opacity-100 transition-all"
            onClick={() => setTheme(isDark ? "light" : "dark")}
            title={isDark ? "Switch to light mode" : "Switch to dark mode"}
        >
            <div className="relative w-3.5 h-3.5">
                <Sun
                    className={`h-3.5 w-3.5 absolute inset-0 transition-all duration-300 ${isDark
                            ? "rotate-0 scale-100 opacity-100"
                            : "-rotate-90 scale-0 opacity-0"
                        }`}
                />
                <Moon
                    className={`h-3.5 w-3.5 absolute inset-0 transition-all duration-300 ${isDark
                            ? "rotate-90 scale-0 opacity-0"
                            : "rotate-0 scale-100 opacity-100"
                        }`}
                />
            </div>
        </Button>
    );
}
