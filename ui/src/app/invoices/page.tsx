"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { FileText, Eye, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function InvoicesPage() {
  const [invoices, setInvoices] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchInvoices();
  }, []);

  const fetchInvoices = async () => {
    try {
      const data = await api.getInvoices(0, 100);
      setInvoices(data.invoices || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "New": return <Badge variant="outline" className="bg-slate-100 text-slate-700 border-slate-200">New</Badge>;
      case "In_Process": return <Badge variant="outline" className="bg-blue-50 text-blue-700 border-blue-200">Processing</Badge>;
      case "Extracted": return <Badge variant="outline" className="bg-purple-50 text-purple-700 border-purple-200">Extracted</Badge>;
      case "Pending_Review": return <Badge variant="outline" className="bg-amber-50 text-amber-700 border-amber-200">Needs Review</Badge>;
      case "Approved": return <Badge variant="outline" className="bg-emerald-50 text-emerald-700 border-emerald-200">Approved</Badge>;
      case "Rejected": return <Badge variant="outline" className="bg-red-50 text-red-700 border-red-200">Rejected</Badge>;
      default: return <Badge variant="outline">{status}</Badge>;
    }
  };

  if (loading) {
    return (
      <div className="flex h-[50vh] items-center justify-center">
        <div className="w-8 h-8 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 animate-in fade-in duration-500">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight mb-2">Invoices</h1>
          <p className="text-muted-foreground">Manage and review extracted invoices.</p>
        </div>
        <Link href="/upload">
          <Button className="bg-primary text-primary-foreground">Upload Invoice</Button>
        </Link>
      </div>

      <Card className="bg-card border-border overflow-hidden shadow-sm">
        <CardHeader className="bg-muted/40 border-b border-border pb-4">
          <CardTitle className="text-lg flex items-center gap-2">
            <FileText className="w-5 h-5 text-primary" />
            Recent Invoices
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader className="bg-muted/20">
              <TableRow className="border-border">
                <TableHead className="w-[100px]">ID</TableHead>
                <TableHead>Filename</TableHead>
                <TableHead>Date</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Confidence</TableHead>
                <TableHead className="text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {invoices.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-center h-32 text-muted-foreground">
                    <div className="flex flex-col items-center justify-center gap-2">
                      <AlertCircle className="w-8 h-8 opacity-20" />
                      <p>No invoices found. Try uploading one.</p>
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                invoices.map((inv) => (
                  <TableRow key={inv.id} className="border-border hover:bg-muted/20 transition-colors group">
                    <TableCell className="font-medium text-muted-foreground">#{inv.id}</TableCell>
                    <TableCell className="font-medium">{inv.filename}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {new Date(inv.created_at).toLocaleDateString()}
                    </TableCell>
                    <TableCell>{getStatusBadge(inv.status)}</TableCell>
                    <TableCell>
                      {inv.confidence_score !== null ? (
                        <div className="flex items-center gap-2">
                          <div className="w-16 h-2 bg-muted rounded-full overflow-hidden">
                            <div 
                              className={`h-full ${inv.confidence_score > 0.8 ? 'bg-emerald-500' : inv.confidence_score > 0.5 ? 'bg-amber-500' : 'bg-red-500'}`}
                              style={{ width: `${Math.round(inv.confidence_score * 100)}%` }}
                            />
                          </div>
                          <span className="text-xs text-muted-foreground">{Math.round(inv.confidence_score * 100)}%</span>
                        </div>
                      ) : (
                        <span className="text-xs text-muted-foreground italic">N/A</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <Link href={`/invoices/${inv.id}`}>
                        <Button variant="ghost" size="sm" className="opacity-0 group-hover:opacity-100 transition-opacity">
                          <Eye className="w-4 h-4 mr-2" /> View
                        </Button>
                      </Link>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
