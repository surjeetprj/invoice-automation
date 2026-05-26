"use client";

import { useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { UploadCloud, File, AlertCircle, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";

export default function UploadPage() {
  const [dragOver, setDragOver] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFileSelection(e.dataTransfer.files[0]);
    }
  };

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      handleFileSelection(e.target.files[0]);
    }
  };

  const handleFileSelection = (selectedFile: File) => {
    const validTypes = ["application/pdf"];
    if (!validTypes.includes(selectedFile.type)) {
      toast.error("Unsupported file format. Only PDF invoices are currently supported.");
      return;
    }
    if (selectedFile.size > 10 * 1024 * 1024) {
      toast.error("File too large. Maximum size is 10MB.");
      return;
    }
    setFile(selectedFile);
  };

  const handleUpload = async () => {
    if (!file) return;
    
    setIsUploading(true);
    const toastId = toast.loading("Processing invoice with AI... This may take a moment.");
    
    try {
      const response = await api.processInvoice(file);
      toast.success(`Invoice processed successfully!`, { id: toastId });
      router.push(`/invoices/${response.id}`);
    } catch (error: any) {
      toast.error(`Error: ${error.message}`, { id: toastId });
      setIsUploading(false);
    }
  };

  return (
    <div className="flex flex-col gap-8 max-w-4xl mx-auto animate-in fade-in duration-500">
      <div>
        <h1 className="text-3xl font-bold tracking-tight mb-2">Upload Invoice</h1>
        <p className="text-muted-foreground">Upload a system-generated PDF invoice for automated AI extraction.</p>
      </div>

      <Card className="bg-card backdrop-blur border-border shadow-sm">

        <CardHeader>
          <CardTitle>File Upload</CardTitle>
          <CardDescription>Drag and drop your file here or click to browse.</CardDescription>
        </CardHeader>
        <CardContent>
          <div 
            className={`border-2 border-dashed rounded-xl p-12 text-center transition-all duration-300 flex flex-col items-center justify-center cursor-pointer
              ${dragOver ? 'border-primary bg-primary/10 scale-[1.02]' : 'border-border hover:border-primary/50 hover:bg-white/5'}
            `}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
          >
            <input 
              type="file" 
              className="hidden" 
              ref={fileInputRef} 
              onChange={handleFileInput}
              accept=".pdf"
            />
            
            <div className="w-20 h-20 bg-gradient-to-br from-primary/20 to-cyan-500/20 rounded-full flex items-center justify-center mb-6">
              <UploadCloud className="w-10 h-10 text-primary" />
            </div>
            <h3 className="text-xl font-bold mb-2">Drop your invoice here</h3>
            <p className="text-muted-foreground text-sm mb-6 max-w-sm">
              Supports system-generated PDFs up to 10MB. The AI will automatically extract and validate data.
            </p>
            <Button variant="outline" className="pointer-events-none">
              Browse Files
            </Button>
          </div>

          {file && (
            <div className="mt-8 p-4 bg-background/50 rounded-lg border border-border flex items-center justify-between animate-in slide-in-from-bottom-4">
              <div className="flex items-center gap-4">
                <div className="w-10 h-10 bg-primary/20 rounded-md flex items-center justify-center">
                  <File className="text-primary w-5 h-5" />
                </div>
                <div>
                  <p className="font-medium text-sm">{file.name}</p>
                  <p className="text-xs text-muted-foreground">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <Button variant="ghost" size="sm" onClick={() => setFile(null)} disabled={isUploading}>
                  Remove
                </Button>
                <Button onClick={handleUpload} disabled={isUploading} className="bg-gradient-to-r from-primary to-cyan-500 text-white border-0">
                  {isUploading ? (
                    <>
                      <div className="w-4 h-4 mr-2 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      Processing...
                    </>
                  ) : (
                    "Process Invoice"
                  )}
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
