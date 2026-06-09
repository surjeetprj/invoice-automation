"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { toast } from "sonner";
import { ArrowLeft, Check, X, RefreshCw, Download, FileText, AlertCircle, ChevronDown, Clock, BarChart, FileJson, FileSpreadsheet, Send } from "lucide-react";
import Link from "next/link";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export default function InvoiceDetailPage() {
  const params = useParams();
  const router = useRouter();
  const [invoice, setInvoice] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Form State for Corrections
  const [formData, setFormData] = useState<any>({});
  const [reviewerName, setReviewerName] = useState("reviewer");
  const [rejectionReason, setRejectionReason] = useState("");

  // Audit Logs, Confirmation and Notification Modals
  const [isReprocessing, setIsReprocessing] = useState(false);
  const [showAuditModal, setShowAuditModal] = useState(false);
  const [auditLogs, setAuditLogs] = useState<any[]>([]);
  const [loadingAuditLogs, setLoadingAuditLogs] = useState(false);
  const [confirmAction, setConfirmAction] = useState<{ type: "reprocess" | "export"; format?: string } | null>(null);
  const [alertMessage, setAlertMessage] = useState<{ title: string; text: string; type?: "success" | "info" } | null>(null);

  useEffect(() => {
    fetchInvoice();
  }, [params.id]);

  const fetchInvoice = async () => {
    try {
      const data = await api.getInvoice(params.id as string);
      setInvoice(data);
      if (data.extracted_data) {
        const rawData = data.extracted_data;
        setFormData({
          ...rawData,
          line_items: rawData.line_items || rawData.items || []
        });
      }
    } catch (e: any) {
      toast.error(e.message || "Failed to load invoice");
    } finally {
      setLoading(false);
    }
  };

  const handleFieldChange = (key: string, value: string) => {
    setFormData((prev: any) => ({ ...prev, [key]: value }));
  };

  const handleItemChange = (index: number, key: string, value: string) => {
    setFormData((prev: any) => {
      const newItems = [...(prev.line_items || [])];
      newItems[index] = { ...newItems[index], [key]: value };
      return { ...prev, line_items: newItems };
    });
  };

  const submitReview = async (decision: "approve" | "approve_with_corrections" | "reject") => {
    setIsSubmitting(true);
    const toastId = toast.loading(`Submitting ${decision.replace(/_/g, " ")}...`);
    
    try {
      const payload: any = { decision, reviewer: reviewerName };
      
      if (decision === "approve_with_corrections") {
        const corrections: any = {};
        for (const key in formData) {
          if (key === "line_items") {
             if (JSON.stringify(formData.line_items) !== JSON.stringify(invoice.extracted_data?.line_items)) {
               corrections.line_items = formData.line_items;
             }
          } else if (key !== "items" && formData[key] !== invoice.extracted_data?.[key]) {
             corrections[key] = formData[key]; 
          }
        }
        payload.corrections = corrections;
      }
      
      if (decision === "reject") {
        payload.rejection_reason = rejectionReason;
      }

      await api.submitReview(params.id as string, payload);
      toast.success("Review submitted successfully", { id: toastId });
      fetchInvoice(); // Reload
    } catch (e: any) {
      toast.error(e.message || "Review failed", { id: toastId });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleReprocess = async () => {
    setIsReprocessing(true);
    setConfirmAction(null);
    const toastId = toast.loading("Reprocessing invoice...");
    try {
      await api.reprocessInvoice(invoice.id);
      await fetchInvoice();
      toast.success("Invoice reprocessed successfully!", { id: toastId });
      setAlertMessage({
        title: "Reprocess Complete",
        text: "Invoice was successfully re-processed by the AI extractor! The latest extracted data is loaded.",
        type: "success"
      });
    } catch (e: any) {
      toast.error(e.message || "Failed to reprocess invoice", { id: toastId });
    } finally {
      setIsReprocessing(false);
    }
  };

  const handleExport = async (format: string) => {
    setConfirmAction(null);
    const toastId = toast.loading(`Exporting invoice in ${format.toUpperCase()}...`);
    
    try {
      const url = `${process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"}/invoices/${invoice.id}/export?format=${format}`;
      
      if (format === "erpnext") {
        const res = await fetch(url, {
          headers: {
            "X-API-Key": "poc-secret-key-change-me"
          }
        });
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || "Failed to export to ERPNext");
        }
        const data = await res.json();
        toast.success("Exported to ERPNext successfully!", { id: toastId });
        setAlertMessage({
          title: "ERPNext Export Successful",
          text: data.message || `Invoice #${invoice.id} successfully pushed to ERPNext with Reference: ${data.erp_reference}`,
          type: "success"
        });
        fetchInvoice(); // Reload to get updated status
      } else {
        const res = await fetch(url, {
          headers: {
            "X-API-Key": "poc-secret-key-change-me"
          }
        });
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || "Failed to download export file");
        }
        const blob = await res.blob();
        
        let ext = format === "json" ? "json" : format === "csv" ? "csv" : "xml";
        const filename = `invoice_${invoice.id}_export.${ext}`;
        
        const downloadUrl = window.URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = downloadUrl;
        link.setAttribute("download", filename);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(downloadUrl);
        
        toast.success("File downloaded successfully!", { id: toastId });
        setAlertMessage({
          title: "Export Completed",
          text: `Invoice #${invoice.id} has been exported in ${format.toUpperCase()} format. The download has been completed successfully.`,
          type: "success"
        });
        fetchInvoice(); // Reload to get updated status
      }
    } catch (e: any) {
      toast.error(e.message || "Export failed", { id: toastId });
    }
  };

  const handleOpenAuditLog = async () => {
    setShowAuditModal(true);
    setLoadingAuditLogs(true);
    try {
      const logs = await api.getInvoiceAuditLog(invoice.id);
      setAuditLogs(logs);
    } catch (e: any) {
      toast.error(e.message || "Failed to load audit logs");
    } finally {
      setLoadingAuditLogs(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-[50vh] items-center justify-center">
        <div className="w-8 h-8 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
      </div>
    );
  }

  if (!invoice) return <div>Invoice not found</div>;

  const isReviewable = invoice.status === "Pending_Review" || invoice.status === "Rejected";

  const renderDataSection = (title: string, fields: {key: string, label: string}[]) => (
    <div className="mb-8">
      <h3 className="text-xs font-bold text-primary uppercase tracking-wider mb-4 border-b border-border pb-2">
        {title}
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {fields.map((f) => (
          <div key={f.key} className="flex flex-col gap-1.5">
            <Label className="text-xs text-muted-foreground uppercase">{f.label}</Label>
            {isReviewable ? (
              <Input 
                value={formData[f.key] || ""} 
                onChange={(e) => handleFieldChange(f.key, e.target.value)}
                className="bg-white border-border focus-visible:ring-primary h-8 text-sm"
              />
            ) : (
              <div className="text-sm font-medium h-8 flex items-center">
                {invoice.extracted_data?.[f.key] || <span className="text-muted-foreground italic">N/A</span>}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );

  const renderItemsSection = () => {
    if (!formData.line_items || formData.line_items.length === 0) return null;
    return (
      <div className="mb-8">
        <h3 className="text-xs font-bold text-primary uppercase tracking-wider mb-4 border-b border-border pb-2">Line Items</h3>
        <div className="flex flex-col gap-4">
          {formData.line_items.map((item: any, idx: number) => (
            <div key={idx} className="p-4 bg-black/25 rounded-xl border border-white/5 flex flex-col gap-4">
              <div className="flex items-center justify-between border-b border-white/5 pb-2">
                <span className="text-xs font-bold text-muted-foreground uppercase">Item #{idx + 1}</span>
                {isReviewable ? (
                  <div className="flex items-center gap-1.5 max-w-[80px]">
                    <Label className="text-[10px] text-muted-foreground uppercase">Sr #</Label>
                    <Input 
                      value={item.sr_no || ""} 
                      onChange={(e) => handleItemChange(idx, "sr_no", e.target.value)} 
                      className="bg-white border-border h-6 text-xs px-1 text-center font-mono" 
                    />
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground font-mono">Sr #{item.sr_no || idx + 1}</span>
                )}
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="flex flex-col gap-1.5 col-span-2 md:col-span-4">
                  <Label className="text-[10px] text-muted-foreground uppercase">Description</Label>
                  {isReviewable ? (
                    <Input 
                      value={item.description || ""} 
                      onChange={(e) => handleItemChange(idx, "description", e.target.value)} 
                      className="bg-white border-border h-8 text-sm" 
                    />
                  ) : (
                    <div className="text-sm font-medium">{item.description || <span className="italic text-muted-foreground">N/A</span>}</div>
                  )}
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label className="text-[10px] text-muted-foreground uppercase">HSN / SAC</Label>
                  {isReviewable ? (
                    <Input 
                      value={item.hsn_sac || ""} 
                      onChange={(e) => handleItemChange(idx, "hsn_sac", e.target.value)} 
                      className="bg-white border-border h-8 text-sm" 
                    />
                  ) : (
                    <div className="text-sm font-medium">{item.hsn_sac || "-"}</div>
                  )}
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label className="text-[10px] text-muted-foreground uppercase">Quantity</Label>
                  {isReviewable ? (
                    <Input 
                      value={item.quantity || ""} 
                      onChange={(e) => handleItemChange(idx, "quantity", e.target.value)} 
                      className="bg-white border-border h-8 text-sm" 
                    />
                  ) : (
                    <div className="text-sm font-medium">{item.quantity || "-"}</div>
                  )}
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label className="text-[10px] text-muted-foreground uppercase">Unit (e.g. PCS, NOS)</Label>
                  {isReviewable ? (
                    <Input 
                      value={item.unit || ""} 
                      onChange={(e) => handleItemChange(idx, "unit", e.target.value)} 
                      className="bg-white border-border h-8 text-sm" 
                    />
                  ) : (
                    <div className="text-sm font-medium">{item.unit || "-"}</div>
                  )}
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label className="text-[10px] text-muted-foreground uppercase">Rate (Unit Price)</Label>
                  {isReviewable ? (
                    <Input 
                      value={item.rate !== undefined ? item.rate : item.unit_price || ""} 
                      onChange={(e) => handleItemChange(idx, "rate", e.target.value)} 
                      className="bg-white border-border h-8 text-sm" 
                    />
                  ) : (
                    <div className="text-sm font-medium">{item.rate !== undefined ? item.rate : item.unit_price || "-"}</div>
                  )}
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label className="text-[10px] text-muted-foreground uppercase">Discount</Label>
                  {isReviewable ? (
                    <Input 
                      value={item.discount || ""} 
                      onChange={(e) => handleItemChange(idx, "discount", e.target.value)} 
                      className="bg-white border-border h-8 text-sm" 
                    />
                  ) : (
                    <div className="text-sm font-medium">{item.discount || "0.0"}</div>
                  )}
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label className="text-[10px] text-muted-foreground uppercase">Taxable Value</Label>
                  {isReviewable ? (
                    <Input 
                      value={item.taxable_value || ""} 
                      onChange={(e) => handleItemChange(idx, "taxable_value", e.target.value)} 
                      className="bg-white border-border h-8 text-sm" 
                    />
                  ) : (
                    <div className="text-sm font-medium">{item.taxable_value || "-"}</div>
                  )}
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label className="text-[10px] text-muted-foreground uppercase">CESS Amount</Label>
                  {isReviewable ? (
                    <Input 
                      value={item.cess_amount || ""} 
                      onChange={(e) => handleItemChange(idx, "cess_amount", e.target.value)} 
                      className="bg-white border-border h-8 text-sm" 
                    />
                  ) : (
                    <div className="text-sm font-medium">{item.cess_amount || "0.0"}</div>
                  )}
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label className="text-[10px] text-muted-foreground uppercase">Total Amount</Label>
                  {isReviewable ? (
                    <Input 
                      value={item.total || ""} 
                      onChange={(e) => handleItemChange(idx, "total", e.target.value)} 
                      className="bg-white border-border h-8 text-sm" 
                    />
                  ) : (
                    <div className="text-sm font-bold text-primary">{item.total || "-"}</div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  };

  const isExportable = invoice.status === "Approved" || invoice.status === "Posted";

  return (
    <div className="flex flex-col gap-6 animate-in fade-in h-[calc(100vh-6rem)]">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0 border-b border-border/50 pb-4">
        <div className="flex items-start gap-4">
          <Link href="/invoices">
            <Button variant="outline" size="icon" className="rounded-full w-8 h-8 mt-1 cursor-pointer">
              <ArrowLeft className="w-4 h-4" />
            </Button>
          </Link>
          <div className="flex flex-col gap-1.5">
            <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
              Invoice #{invoice.id} {invoice.extracted_data?.invoice_number && <span className="text-muted-foreground font-normal">— {invoice.extracted_data.invoice_number}</span>}
            </h1>
            <div className="flex items-center gap-4 text-sm flex-wrap">
              <Badge variant="outline" className="bg-primary/10 text-primary border-primary/20 uppercase tracking-wider text-[10px] h-5 rounded-md px-1.5">
                {invoice.status}
              </Badge>
              <div className="flex items-center gap-1.5 text-muted-foreground font-medium">
                <Clock className="w-3.5 h-3.5" />
                {invoice.processing_time_ms ? `${(invoice.processing_time_ms / 1000).toFixed(2)}s` : "0.00s"}
              </div>
              {invoice.confidence_score !== null && (
                <div className="flex items-center gap-2 text-muted-foreground font-medium">
                  <BarChart className="w-3.5 h-3.5" />
                  <div className="w-16 h-1.5 bg-white/10 rounded-full overflow-hidden">
                    <div 
                      className={`h-full ${invoice.confidence_score > 0.8 ? 'bg-emerald-500' : invoice.confidence_score > 0.5 ? 'bg-amber-500' : 'bg-red-500'}`}
                      style={{ width: `${Math.round(invoice.confidence_score * 100)}%` }}
                    />
                  </div>
                  <span>{Math.round(invoice.confidence_score * 100)}%</span>
                </div>
              )}
              <div className="flex items-center gap-1.5 text-muted-foreground font-medium">
                <FileText className="w-3.5 h-3.5" />
                {invoice.filename}
              </div>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 self-start mt-1">
          <DropdownMenu>
            <DropdownMenuTrigger 
              disabled={isReprocessing || isSubmitting}
              className={cn(buttonVariants({ variant: "outline", size: "sm" }), "cursor-pointer", (isReprocessing || isSubmitting) && "opacity-50 pointer-events-none")}
            >
              <>
                {!isExportable && <span className="mr-1.5 opacity-70">🔒</span>}
                Export 
                <ChevronDown className="w-4 h-4 ml-2 opacity-50" />
              </>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className={cn(isExportable ? "w-48 animate-in fade-in duration-100" : "w-64 animate-in fade-in duration-100", "bg-card border-border text-foreground shadow-lg")}>
              {isExportable ? (
                <DropdownMenuGroup>
                  <DropdownMenuLabel>Export Formats</DropdownMenuLabel>
                  <DropdownMenuSeparator className="bg-white/10" />
                  <DropdownMenuItem className="cursor-pointer hover:bg-white/10 focus:bg-white/10" onClick={() => setConfirmAction({ type: "export", format: "json" })}>
                    <FileJson className="w-4 h-4 mr-2 text-yellow-400" /> JSON Format
                  </DropdownMenuItem>
                  <DropdownMenuItem className="cursor-pointer hover:bg-white/10 focus:bg-white/10" onClick={() => setConfirmAction({ type: "export", format: "csv" })}>
                    <FileSpreadsheet className="w-4 h-4 mr-2 text-green-400" /> CSV Format
                  </DropdownMenuItem>
                  <DropdownMenuItem className="cursor-pointer hover:bg-white/10 focus:bg-white/10" onClick={() => setConfirmAction({ type: "export", format: "tally" })}>
                    <Send className="w-4 h-4 mr-2 text-orange-400" /> Tally XML
                  </DropdownMenuItem>
                  <DropdownMenuSeparator className="bg-white/10" />
                  <DropdownMenuItem className="cursor-pointer hover:bg-white/10 focus:bg-white/10 text-cyan-400 focus:text-cyan-400" onClick={() => setConfirmAction({ type: "export", format: "erpnext" })}>
                    <Send className="w-4 h-4 mr-2" /> Export to ERPNext
                  </DropdownMenuItem>
                </DropdownMenuGroup>
              ) : (
                <div className="flex flex-col items-center text-center p-3.5 gap-2">
                  <div className="w-8 h-8 rounded-full bg-amber-500/10 flex items-center justify-center border border-amber-500/25">
                    <span className="text-sm">🔒</span>
                  </div>
                  <h4 className="text-xs font-bold text-amber-400 uppercase tracking-wider">Approval Required</h4>
                  <p className="text-[11.5px] text-muted-foreground leading-relaxed">
                    This invoice is currently in <span className="font-semibold text-foreground uppercase">{invoice.status}</span> status. Complete human review and approve it to unlock export formats.
                  </p>
                </div>
              )}
            </DropdownMenuContent>
          </DropdownMenu>

          <Button 
            variant="outline" 
            size="sm" 
            className="cursor-pointer" 
            disabled={isReprocessing || isSubmitting}
            onClick={() => setConfirmAction({ type: "reprocess" })}
          >
            <RefreshCw className={cn("w-4 h-4 mr-2", isReprocessing && "animate-spin")} /> 
            {isReprocessing ? "Reprocessing..." : "Reprocess"}
          </Button>

          <Button 
            variant="outline" 
            size="sm" 
            className="cursor-pointer font-medium text-foreground bg-card border-border hover:bg-secondary hover:text-foreground shadow-sm transition-all"
            disabled={isReprocessing || isSubmitting}
            onClick={handleOpenAuditLog}
          >
            <FileText className="w-4 h-4 mr-2 text-primary" /> Audit Log
          </Button>
        </div>
      </div>

      {/* Resizable Split Pane View */}
      <ResizablePanelGroup direction="horizontal" className="flex-1 min-h-0 w-full rounded-xl border border-border bg-card/40 shadow-md overflow-hidden">
        
        {/* Left Column: Data & Validation */}
        <ResizablePanel defaultSize={50} minSize={30} className="flex flex-col min-h-0 bg-card/40">
          <div className="h-full overflow-y-auto p-4 custom-scrollbar">
            <Tabs defaultValue="data" className="w-full">
              <TabsList className="w-full border border-border p-1 mb-4 h-12 sticky top-0 z-10 backdrop-blur-md bg-muted/60">
                <TabsTrigger value="data" className="flex-1 data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm">Extracted Data</TabsTrigger>
                <TabsTrigger value="validation" className="flex-1 data-[state=active]:bg-card data-[state=active]:text-foreground data-[state=active]:shadow-sm">Validation</TabsTrigger>
                <TabsTrigger value="raw" className="flex-1 data-[state=active]:bg-card data-[state=active]:text-foreground data-[state=active]:shadow-sm">Raw Text</TabsTrigger>
              </TabsList>
              
              <TabsContent value="data" className="m-0 border-none p-0 outline-none">
                <Card className="bg-transparent border-none shadow-none">
                  <CardContent className="p-0">
                    {renderDataSection("Invoice Header", [
                      {key: "invoice_number", label: "Invoice #"},
                      {key: "date", label: "Date"},
                      {key: "due_date", label: "Due Date"},
                      {key: "supply_type", label: "Supply Type"},
                      {key: "challan_no", label: "Challan #"},
                      {key: "challan_date", label: "Challan Date"},
                      {key: "e_way_bill_no", label: "E-Way Bill #"},
                      {key: "reverse_charge", label: "Reverse Charge [Y/N]"},
                    ])}

                    {renderDataSection("E-Invoicing Details", [
                      {key: "irn", label: "IRN (Invoice Ref. Number)"},
                      {key: "ack_number", label: "Ack Number"},
                      {key: "ack_date", label: "Ack Date"},
                      {key: "qr_code_data", label: "QR Code Data"},
                    ])}
                    
                    {renderDataSection("Vendor Details", [
                      {key: "vendor_name", label: "Name"},
                      {key: "vendor_gstin", label: "GSTIN"},
                      {key: "vendor_address", label: "Address"},
                      {key: "vendor_state_code", label: "State Code"},
                      {key: "vendor_pan", label: "PAN"},
                      {key: "vendor_msme_no", label: "MSME Number"},
                      {key: "vendor_contact", label: "Contact / Phone / Email"},
                    ])}

                    {renderDataSection("Customer Details", [
                      {key: "customer_name", label: "Name"},
                      {key: "customer_gstin", label: "GSTIN"},
                      {key: "customer_address", label: "Address"},
                      {key: "customer_state_code", label: "State Code"},
                      {key: "customer_pan", label: "PAN"},
                      {key: "customer_phone", label: "Phone / Contact"},
                      {key: "place_of_supply", label: "Place of Supply"},
                    ])}

                    {renderDataSection("Shipping Details", [
                      {key: "shipping_name", label: "Name"},
                      {key: "shipping_gstin", label: "GSTIN"},
                      {key: "shipping_address", label: "Address"},
                    ])}

                    {renderDataSection("Transport Details", [
                      {key: "transport_name", label: "Transporter Name"},
                      {key: "transport_id", label: "Transporter ID"},
                      {key: "vehicle_number", label: "Vehicle Number"},
                    ])}

                    {renderItemsSection()}
                    
                    {renderDataSection("Tax Summary", [
                      {key: "total_taxable_amount", label: "Taxable Amount"},
                      {key: "total_cgst", label: "Total CGST"},
                      {key: "total_sgst", label: "Total SGST"},
                      {key: "total_igst", label: "Total IGST"},
                      {key: "total_cess", label: "Total CESS"},
                      {key: "total_tax_amount", label: "Total Tax Amount"},
                      {key: "round_off", label: "Round Off"},
                      {key: "total_amount", label: "Grand Total"},
                      {key: "amount_in_words", label: "Amount in Words"},
                    ])}

                    {renderDataSection("Bank Details", [
                      {key: "bank_name", label: "Bank Name"},
                      {key: "account_no", label: "Account Number"},
                      {key: "ifsc", label: "IFSC Code"},
                      {key: "branch", label: "Branch Name"},
                    ])}
                  </CardContent>
                </Card>

                {/* Review Panel */}
                {isReviewable && (
                  <Card className="bg-accent/30 border-primary/20 mt-6 shadow-sm">
                    <CardHeader>
                      <CardTitle className="text-lg">Human Review</CardTitle>
                    </CardHeader>
                    <CardContent className="flex flex-col gap-4">
                      <div>
                        <Label>Reviewer Name</Label>
                        <Input 
                          value={reviewerName} 
                          onChange={(e) => setReviewerName(e.target.value)} 
                          className="bg-white/5 border-white/10 mt-1 max-w-[300px]"
                        />
                      </div>
                      <div className="flex flex-wrap gap-3 mt-2">
                        <Button onClick={() => submitReview("approve")} disabled={isSubmitting} className="bg-emerald-500 hover:bg-emerald-600 text-white border-none cursor-pointer">
                          <Check className="w-4 h-4 mr-2" /> Approve (No Changes)
                        </Button>
                        <Button onClick={() => submitReview("approve_with_corrections")} disabled={isSubmitting} className="bg-amber-500 hover:bg-amber-600 text-white border-none cursor-pointer">
                          <Check className="w-4 h-4 mr-2" /> Submit Corrections
                        </Button>
                        <div className="flex items-center gap-2 w-full mt-2">
                          <Input 
                            placeholder="Rejection Reason..." 
                            value={rejectionReason}
                            onChange={(e) => setRejectionReason(e.target.value)}
                            className="bg-white/5 border-white/10 flex-1"
                          />
                          <Button onClick={() => submitReview("reject")} variant="destructive" disabled={isSubmitting || !rejectionReason} className="cursor-pointer border-none">
                            <X className="w-4 h-4 mr-2" /> Reject
                          </Button>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                )}
              </TabsContent>

              <TabsContent value="validation" className="m-0">
                <Card className="bg-transparent border-none shadow-none">
                  <CardContent className="p-0 flex flex-col gap-3">
                    {invoice.validation?.issues?.length > 0 ? (
                      invoice.validation.issues.map((issue: any, i: number) => (
                        <div key={i} className={`p-4 rounded-lg border flex items-start gap-3 transition-colors ${
                          issue.severity === 'error' 
                            ? 'bg-red-50 dark:bg-red-950/20 border-red-200 dark:border-red-900/30 text-red-700 dark:text-red-200' 
                            : 'bg-amber-50 dark:bg-amber-950/20 border-amber-200 dark:border-amber-900/30 text-amber-800 dark:text-amber-200'
                        }`}>
                          <AlertCircle className={`w-5 h-5 shrink-0 ${issue.severity === 'error' ? 'text-red-600 dark:text-red-400' : 'text-amber-600 dark:text-amber-400'}`} />
                          <div>
                            <p className="text-sm font-medium">{issue.message}</p>
                            <p className="text-xs opacity-70 uppercase tracking-wider mt-1">{issue.field}</p>
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="text-center p-8 text-emerald-400">
                        <CheckCircle2 className="w-12 h-12 mx-auto mb-3 opacity-50" />
                        <p>All validation checks passed successfully.</p>
                      </div>
                    )}
                  </CardContent>
                </Card>
              </TabsContent>
              
              <TabsContent value="raw" className="m-0">
                 <Card className="bg-transparent border-none shadow-none">
                  <CardContent className="p-0">
                    <pre className="text-xs font-mono bg-black/40 p-4 rounded-lg overflow-x-auto text-muted-foreground border border-white/5">
                      {invoice.raw_markdown || "No raw text available"}
                    </pre>
                  </CardContent>
                </Card>
              </TabsContent>
            </Tabs>
          </div>
        </ResizablePanel>

        <ResizableHandle withHandle className="w-2 bg-black/50 hover:bg-primary/50 transition-colors cursor-col-resize z-50 border-x border-white/5" />

        {/* Right Column: Native PDF/Image Viewer */}
        <ResizablePanel defaultSize={50} minSize={30} className="flex flex-col min-h-0 bg-white">
          <div className="bg-[#111827] border-b border-white/5 p-3 flex items-center justify-between shrink-0">
            <span className="text-sm font-medium flex items-center gap-2 text-foreground">
              <FileText className="w-4 h-4 text-primary" /> Source Document
            </span>
            <a href={api.getFileUrl(invoice.id)} target="_blank" rel="noreferrer">
              <Button variant="ghost" size="sm" className="h-8 cursor-pointer">
                <Download className="w-4 h-4 mr-2" /> Download
              </Button>
            </a>
          </div>
          <iframe 
            src={api.getFileUrl(invoice.id)} 
            className="w-full flex-1 border-0"
            title="Invoice Document"
          />
        </ResizablePanel>
        
      </ResizablePanelGroup>

      {/* Audit Log Modal */}
      {showAuditModal && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="bg-card border border-border w-full max-w-2xl rounded-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 flex flex-col max-h-[85vh] backdrop-blur-md">
            {/* Header */}
            <div className="p-6 border-b border-border flex items-center justify-between shrink-0 bg-slate-50">
              <div>
                <h2 className="text-xl font-bold tracking-tight text-foreground flex items-center gap-2">
                  <FileText className="w-5 h-5 text-primary" /> Invoice Audit Trail
                </h2>
                <p className="text-xs text-muted-foreground mt-1">Full history of events for Invoice #{invoice.id}</p>
              </div>
              <button 
                onClick={() => setShowAuditModal(false)}
                className="text-muted-foreground hover:text-foreground hover:bg-slate-200 p-1.5 rounded-full transition-colors cursor-pointer"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Content */}
            <div className="overflow-y-auto p-6 flex-1 custom-scrollbar">
              {loadingAuditLogs ? (
                <div className="flex h-40 items-center justify-center flex-col gap-3">
                  <div className="w-8 h-8 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
                  <span className="text-sm text-muted-foreground font-medium">Fetching audit history...</span>
                </div>
              ) : auditLogs.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-gray-400 gap-2">
                  <FileText className="w-10 h-10 opacity-20" />
                  <p className="text-sm font-medium">No audit logs found for this invoice.</p>
                </div>
              ) : (
                <div className="relative pl-6 border-l border-border ml-3 space-y-8 py-2">
                  {auditLogs.map((log) => {
                    let dotColor = "bg-blue-600 border-blue-200";
                    let pillBg = "bg-blue-50 text-blue-700 border-blue-200";
                    
                    const actionLower = log.action.toLowerCase();
                    if (actionLower.includes("approve") || actionLower.includes("accept")) {
                      dotColor = "bg-emerald-500 border-emerald-500/30";
                      pillBg = "bg-emerald-500/10 text-emerald-400 border-emerald-500/20";
                    } else if (actionLower.includes("reject")) {
                      dotColor = "bg-red-500 border-red-500/30";
                      pillBg = "bg-red-500/10 text-red-400 border-red-500/20";
                    } else if (actionLower.includes("process")) {
                      dotColor = "bg-cyan-500 border-cyan-500/30";
                      pillBg = "bg-cyan-500/10 text-cyan-400 border-cyan-500/20";
                    } else if (actionLower.includes("correct") || actionLower.includes("update") || actionLower.includes("edit")) {
                      dotColor = "bg-amber-500 border-amber-500/30";
                      pillBg = "bg-amber-500/10 text-amber-400 border-amber-500/20";
                    }

                    return (
                      <div key={log.id} className="relative group">
                        <div className={`absolute -left-[31px] top-1.5 w-4 h-4 rounded-full border-4 border-card ${dotColor} z-10 flex items-center justify-center shadow-lg group-hover:scale-110 transition-transform`} />
                        
                        <div className="flex flex-col gap-1.5 bg-slate-50 border border-border p-4 rounded-xl hover:border-primary/20 hover:bg-slate-100/50 transition-all">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-md border ${pillBg}`}>
                              {log.action}
                            </span>
                            <span className="text-[10px] text-muted-foreground font-medium">
                              {log.timestamp ? new Date(log.timestamp).toLocaleString() : "N/A"}
                            </span>
                          </div>

                          <div className="flex items-center gap-1.5 text-sm text-foreground mt-1">
                            <span className="text-muted-foreground">Performed by:</span>
                            <span className="font-semibold text-primary">{log.user || "system"}</span>
                          </div>

                          {log.reason && (
                            <div className="text-xs text-muted-foreground bg-slate-100 p-2.5 rounded-lg border border-border mt-2 font-mono whitespace-pre-wrap">
                              {log.reason}
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="p-4 border-t border-border flex justify-end shrink-0 bg-slate-50">
              <Button variant="outline" onClick={() => setShowAuditModal(false)} className="cursor-pointer">
                Close
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Confirmation Modal */}
      {confirmAction && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="bg-card/95 border border-white/10 w-full max-w-md rounded-2xl shadow-2xl overflow-hidden p-6 animate-in zoom-in-95 duration-200 flex flex-col gap-4 backdrop-blur-md">
            <div className="flex items-start gap-4">
              <div className="w-10 h-10 rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
                <AlertCircle className="w-5 h-5 text-primary" />
              </div>
              <div className="flex-1">
                <h3 className="text-lg font-bold text-foreground">Confirm Action</h3>
                <p className="text-sm text-muted-foreground mt-2 leading-relaxed">
                  {confirmAction.type === "reprocess" ? (
                    "Are you sure you want to re-process this invoice? All unsubmitted manual corrections will be discarded, and the AI model will re-analyze the source document."
                  ) : (
                    `Are you sure you want to export this invoice in ${confirmAction.format?.toUpperCase()} format?`
                  )}
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-3 mt-2">
              <Button 
                variant="outline" 
                onClick={() => setConfirmAction(null)}
                className="cursor-pointer"
              >
                Cancel
              </Button>
              <Button 
                className="bg-primary hover:bg-primary/90 text-primary-foreground border-none cursor-pointer"
                onClick={() => {
                  if (confirmAction.type === "reprocess") {
                    handleReprocess();
                  } else if (confirmAction.type === "export") {
                    handleExport(confirmAction.format!);
                  }
                }}
              >
                Confirm
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Success Notification Alert */}
      {alertMessage && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="bg-card/95 border border-white/10 w-full max-w-md rounded-2xl shadow-2xl overflow-hidden p-6 animate-in zoom-in-95 duration-200 flex flex-col gap-4 backdrop-blur-md">
            <div className="flex items-start gap-4">
              <div className="w-10 h-10 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center shrink-0">
                <CheckCircle2 className="w-5 h-5 text-emerald-400" />
              </div>
              <div className="flex-1">
                <h3 className="text-lg font-bold text-foreground">{alertMessage.title}</h3>
                <p className="text-sm text-muted-foreground mt-2 leading-relaxed">
                  {alertMessage.text}
                </p>
              </div>
            </div>
            <div className="flex justify-end mt-2">
              <Button 
                className="bg-emerald-500 hover:bg-emerald-600 text-white border-none cursor-pointer px-6"
                onClick={() => setAlertMessage(null)}
              >
                OK
              </Button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}

// Needed because CheckCircle2 is missing in import above
function CheckCircle2(props: any) {
  return (
    <svg
      {...props}
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="10" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}
