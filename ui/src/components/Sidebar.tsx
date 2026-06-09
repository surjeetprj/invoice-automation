"use client";

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { LayoutDashboard, FileUp, List, ChevronLeft, ChevronRight, Sun, Moon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useTheme } from 'next-themes';

export function Sidebar() {
  const [isOpen, setIsOpen] = useState(true);
  const [mounted, setMounted] = useState(false);
  const { theme, setTheme } = useTheme();

  useEffect(() => {
    setMounted(true);
  }, []);

  return (
    <aside 
      className={`${isOpen ? 'w-64' : 'w-20'} transition-all duration-300 border-r border-border bg-card/50 backdrop-blur-md min-h-screen p-4 flex flex-col gap-8 sticky top-0 shrink-0 group relative`}
    >
      <Button 
        variant="outline" 
        size="icon"
        className="absolute -right-4 top-8 z-50 rounded-full w-8 h-8 shadow-md border-border bg-card hover:bg-accent"
        onClick={() => setIsOpen(!isOpen)}
      >
        {isOpen ? <ChevronLeft className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
      </Button>

      <div className="flex items-center gap-3 overflow-hidden px-2 mt-2">
        <div className="w-10 h-10 shrink-0 rounded-xl bg-gradient-to-br from-primary to-cyan-500 flex items-center justify-center shadow-[0_0_15px_rgba(139,92,246,0.3)]">
          <span className="text-white font-bold text-xl">IA</span>
        </div>
        {isOpen && (
          <div className="flex flex-col whitespace-nowrap animate-in fade-in duration-300">
            <span className="font-bold text-lg tracking-tight">Invoice AI</span>
            <span className="text-xs text-muted-foreground font-medium">Enterprise v2.0</span>
          </div>
        )}
      </div>
      
      <nav className="flex flex-col gap-2 flex-1 mt-4">
        <Link href="/" className={`flex items-center gap-3 ${isOpen ? 'px-4' : 'justify-center'} py-3 rounded-lg hover:bg-accent hover:text-accent-foreground transition-colors font-medium text-sm text-muted-foreground`} title="Dashboard">
          <LayoutDashboard className="h-5 w-5 shrink-0" /> 
          {isOpen && <span className="whitespace-nowrap animate-in fade-in duration-300">Dashboard</span>}
        </Link>
        <Link href="/upload" className={`flex items-center gap-3 ${isOpen ? 'px-4' : 'justify-center'} py-3 rounded-lg hover:bg-accent hover:text-accent-foreground transition-colors font-medium text-sm text-muted-foreground`} title="Upload Invoice">
          <FileUp className="h-5 w-5 shrink-0" /> 
          {isOpen && <span className="whitespace-nowrap animate-in fade-in duration-300">Upload Invoice</span>}
        </Link>
        <Link href="/invoices" className={`flex items-center gap-3 ${isOpen ? 'px-4' : 'justify-center'} py-3 rounded-lg hover:bg-accent hover:text-accent-foreground transition-colors font-medium text-sm text-muted-foreground`} title="Invoices & Review">
          <List className="h-5 w-5 shrink-0" /> 
          {isOpen && <span className="whitespace-nowrap animate-in fade-in duration-300">Invoices & Review</span>}
        </Link>
      </nav>

      {/* Theme Switcher Button */}
      <div className={`mt-auto border-t border-border pt-4 flex flex-col gap-2`}>
        <Button
          variant="ghost"
          onClick={() => {
            if (mounted) setTheme(theme === 'dark' ? 'light' : 'dark');
          }}
          className={`w-full flex items-center ${isOpen ? 'justify-start px-3' : 'justify-center'} py-3 rounded-lg hover:bg-accent hover:text-accent-foreground transition-colors font-medium text-sm text-muted-foreground`}
          title={mounted && theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
        >
          {!mounted ? (
            <>
              <div className="h-5 w-5 shrink-0 rounded-full bg-muted animate-pulse" />
              {isOpen && <div className="h-4 w-20 bg-muted rounded animate-pulse" />}
            </>
          ) : theme === 'dark' ? (
            <>
              <Sun className="h-5 w-5 shrink-0 text-amber-500" />
              {isOpen && <span className="whitespace-nowrap animate-in fade-in duration-300">Light Mode</span>}
            </>
          ) : (
            <>
              <Moon className="h-5 w-5 shrink-0 text-indigo-500" />
              {isOpen && <span className="whitespace-nowrap animate-in fade-in duration-300">Dark Mode</span>}
            </>
          )}
        </Button>
      </div>

      <div className={`border-t border-border pt-4 flex ${isOpen ? 'justify-start px-2' : 'justify-center'}`}>
        <div className="flex items-center gap-2 text-xs text-muted-foreground font-medium" title="Backend Online">
          <div className="w-2 h-2 shrink-0 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]"></div>
          {isOpen && <span className="whitespace-nowrap animate-in fade-in duration-300">Backend Online</span>}
        </div>
      </div>
    </aside>
  );
}
