"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FileText, Clock, CheckCircle, AlertTriangle } from "lucide-react";

export default function Dashboard() {
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getStats()
      .then(setStats)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="w-8 h-8 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
      </div>
    );
  }

  const accuracy = stats?.total_invoices > 0 
    ? Math.round((stats.total_approved / stats.total_invoices) * 100) 
    : 0;

  return (
    <div className="flex flex-col gap-8 animate-in fade-in duration-500">
      <div>
        <h1 className="text-3xl font-bold tracking-tight mb-2">Dashboard</h1>
        <p className="text-muted-foreground">Overview of your invoice processing pipeline.</p>
      </div>

      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-4">
        <Card className="bg-card backdrop-blur border-border hover:border-primary/20 shadow-sm hover:shadow-md transition-all">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground uppercase tracking-wider">
              Total Invoices
            </CardTitle>
            <FileText className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold bg-gradient-to-r from-primary to-cyan-400 bg-clip-text text-transparent">
              {stats?.total_invoices || 0}
            </div>
            <p className="text-xs text-muted-foreground mt-1">Processed by system</p>
          </CardContent>
        </Card>

        <Card className="bg-card backdrop-blur border-border hover:border-primary/20 shadow-sm hover:shadow-md transition-all">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground uppercase tracking-wider">
              Avg Processing Time
            </CardTitle>
            <Clock className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-foreground">
              {Math.round(stats?.avg_processing_time_ms || 0)} <span className="text-lg">ms</span>
            </div>
            <p className="text-xs text-muted-foreground mt-1">Per document</p>
          </CardContent>
        </Card>

        <Card className="bg-card backdrop-blur border-border hover:border-primary/20 shadow-sm hover:shadow-md transition-all">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground uppercase tracking-wider">
              Accuracy Rate
            </CardTitle>
            <CheckCircle className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-emerald-400">
              {accuracy}%
            </div>
            <p className="text-xs text-muted-foreground mt-1">No human corrections</p>
          </CardContent>
        </Card>

        <Card className="bg-card backdrop-blur border-border hover:border-primary/20 shadow-sm hover:shadow-md transition-all">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground uppercase tracking-wider">
              Pending Review
            </CardTitle>
            <AlertTriangle className="h-4 w-4 text-amber-400" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-amber-400">
              {stats?.status_distribution?.Pending_Review || 0}
            </div>
            <p className="text-xs text-muted-foreground mt-1">Requires attention</p>
          </CardContent>
        </Card>
      </div>

      <Card className="bg-card backdrop-blur border-border shadow-sm">
        <CardHeader>
          <CardTitle>Status Distribution</CardTitle>
        </CardHeader>
        <CardContent>
          {/* Simple Distribution Bar */}
          <div className="h-4 rounded-full flex overflow-hidden bg-white/5 w-full">
            {Object.entries(stats?.status_distribution || {}).map(([status, count]: [string, any]) => {
              if (count === 0) return null;
              const percentage = (count / stats.total_invoices) * 100;
              let color = "bg-primary";
              if (status === "Approved") color = "bg-emerald-500";
              if (status === "Rejected") color = "bg-red-500";
              if (status === "Pending_Review") color = "bg-amber-500";
              if (status === "Extracted") color = "bg-purple-500";
              
              return (
                <div 
                  key={status} 
                  style={{ width: `${percentage}%` }} 
                  className={`h-full ${color} transition-all`}
                  title={`${status}: ${count}`}
                />
              );
            })}
          </div>
          
          {/* Legend */}
          <div className="flex flex-wrap gap-4 mt-6">
            {Object.entries(stats?.status_distribution || {}).map(([status, count]: [string, any]) => {
              let dotColor = "bg-primary";
              if (status === "Approved") dotColor = "bg-emerald-500";
              if (status === "Rejected") dotColor = "bg-red-500";
              if (status === "Pending_Review") dotColor = "bg-amber-500";
              if (status === "Extracted") dotColor = "bg-purple-500";

              return (
                <div key={status} className="flex items-center gap-2 text-sm text-muted-foreground">
                  <div className={`w-3 h-3 rounded-full ${dotColor}`}></div>
                  {status.replace("_", " ")} ({count})
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
