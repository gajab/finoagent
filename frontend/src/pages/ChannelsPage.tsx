import React, { useState, useEffect } from 'react';
import { Share2, AlertCircle, Server, MessageSquare, RefreshCw, CheckCircle2, Save } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { fetchWhatsAppStatus, fetchWhatsAppQR, saveWhatsApp } from '../api';

export default function ChannelsPage() {
  const { user } = useAuth();
  const [waStatus, setWaStatus] = useState<'loading' | 'pending' | 'connected' | 'disabled'>('disabled');
  const [qrCode, setQrCode] = useState<string | null>(null);

  const [phoneNumber, setPhoneNumber] = useState(user?.whatsapp_number || '');
  const [isSavingPhone, setIsSavingPhone] = useState(false);
  const [saveMessage, setSaveMessage] = useState({ text: '', type: '' });

  useEffect(() => {
    if (user?.whatsapp_number) {
      setPhoneNumber(user.whatsapp_number);
    }
  }, [user]);

  useEffect(() => {
    // The WhatsApp integration docker container is currently disabled for performance.
    // Skip backend polling.
    return;
  }, []);

  const handleSavePhone = async () => {
    setIsSavingPhone(true);
    setSaveMessage({ text: '', type: '' });
    try {
      await saveWhatsApp(phoneNumber);
      setSaveMessage({ text: 'Saved successfully', type: 'success' });
      // Clear success message after 3 seconds
      setTimeout(() => setSaveMessage({ text: '', type: '' }), 3000);
      
      // Update local storage user if needed, but the next fetchMe will get it.
    } catch (err: any) {
      setSaveMessage({ text: err.message || 'Failed to save', type: 'error' });
    } finally {
      setIsSavingPhone(false);
    }
  };

  return (
    <div className="container-app py-6 sm:py-8 space-y-6 animate-fade-in">
      {/* Header */}
      <div className="page-header">
        <h1 className="page-title tracking-tight flex items-center gap-3">
          <Share2 className="w-8 h-8 text-primary" />
          Channel Integrations
        </h1>
        <p className="page-subtitle">
          Connect SqubeFi with your favorite messaging platforms to chat with your autonomous AI agents.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 pt-4">
        
        {/* WhatsApp Card */}
        <div className="glass-card overflow-hidden hover:shadow-2xl transition-all duration-300">
          <div className="h-2 bg-[#25D366] w-full"></div>
          <div className="card-body">
            <h2 className="card-title flex items-center gap-2 mb-2 text-2xl">
              <MessageSquare className="text-[#25D366] w-6 h-6" />
              WhatsApp
            </h2>
            <p className="text-sm text-base-content/70 mb-4">
              Connect your personal WhatsApp account. Scan the QR code to allow your agents to message you directly!
            </p>

            {/* Phone Number Input */}
            <div className="form-control w-full mb-4">
              <label className="label pt-0 pb-1">
                <span className="label-text font-medium">Your WhatsApp Number</span>
                <span className="label-text-alt text-base-content/60">Required for routing</span>
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="e.g. +1234567890"
                  className="input input-bordered w-full rounded-xl"
                  value={phoneNumber}
                  onChange={(e) => setPhoneNumber(e.target.value)}
                  disabled={isSavingPhone}
                />
                <button
                  className="btn btn-primary rounded-xl"
                  onClick={handleSavePhone}
                  disabled={isSavingPhone || !phoneNumber.trim()}
                >
                  {isSavingPhone ? <span className="loading loading-spinner loading-sm"></span> : <Save className="w-4 h-4" />}
                  Save
                </button>
              </div>
              {saveMessage.text && (
                <div className={`mt-2 text-sm ${saveMessage.type === 'success' ? 'text-success' : 'text-error'}`}>
                  {saveMessage.text}
                </div>
              )}
            </div>
            
            <div className="flex flex-col items-center justify-center p-6 metric-card min-h-[250px]">
              {waStatus === 'disabled' && (
                <div className="flex flex-col items-center gap-3 text-center">
                  <div className="p-3 bg-warning/10 text-warning rounded-full border border-warning/20 mb-2">
                    <Server className="w-8 h-8" />
                  </div>
                  <span className="font-semibold text-lg drop-shadow-sm text-base-content/80">Service Unavailable</span>
                  <p className="text-xs text-base-content/60 max-w-[220px]">
                    The WhatsApp companion service is currently disabled to improve core application performance. We are working on optimizing this integration!
                  </p>
                </div>
              )}
              
              {waStatus === 'pending' && (
                <div className="flex flex-col items-center w-full gap-4">
                  {qrCode ? (
                    <div className="bg-white p-3 rounded-xl shadow-inner">
                      <img 
                        src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(qrCode)}`}
                        alt="WhatsApp QR Code"
                        className="w-48 h-48"
                      />
                    </div>
                  ) : (
                    <div className="w-48 h-48 bg-base-300 rounded-xl flex items-center justify-center animate-pulse">
                      <RefreshCw className="w-8 h-8 opacity-50 animate-spin" />
                    </div>
                  )}
                  <div className="text-center">
                    <span className="font-semibold text-lg drop-shadow-sm">Scan to Connect</span>
                    <p className="text-xs opacity-70 mt-1 max-w-[200px]">Open WhatsApp on your phone &gt; Settings &gt; Linked Devices</p>
                  </div>
                </div>
              )}

              {waStatus === 'connected' && (
                <div className="flex flex-col items-center gap-3 bg-success/10 text-success p-6 rounded-xl border border-success/30 w-full animate-in zoom-in duration-300">
                  <CheckCircle2 className="w-16 h-16 drop-shadow-md" />
                  <span className="font-bold text-xl drop-shadow-sm">Connected successfully!</span>
                  <span className="text-sm opacity-90 text-center">Your companion node service is running.</span>
                </div>
              )}
            </div>

            <div className="divider my-2"></div>
            
            <div className="text-xs text-base-content/60 metric-card p-3">
                <span className="font-semibold block mb-1">How it works:</span>
                1. Save your WhatsApp number above.<br/>
                2. Point your phone at the QR code to connect the node.<br/>
                3. Once connected, send <code className="bg-base-300 px-1 rounded">Agents</code> to yourself on WhatsApp.<br/>
                4. The internal service routes it to your AI!
            </div>
          </div>
        </div>

        {/* Telegram Card (Coming Soon) */}
        <div className="glass-card opacity-60 grayscale hover:grayscale-0 transition-all duration-300">
          <div className="h-2 bg-[#0088cc] w-full"></div>
          <div className="card-body">
            <h2 className="card-title flex items-center gap-2 mb-2 text-2xl">
              <svg viewBox="0 0 24 24" className="w-6 h-6 fill-[#0088cc]"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69.01-.03.01-.14-.07-.19-.08-.05-.19-.02-.27 0-.12.03-1.98 1.26-5.59 3.7-.53.36-1.01.54-1.44.53-.47-.01-1.38-.27-2.05-.48-.82-.27-1.47-.41-1.41-.87.03-.24.36-.49 1-.76 3.91-1.7 6.52-2.82 7.82-3.36 3.73-1.55 4.51-1.82 5.01-1.83.11 0 .36.03.49.14.11.09.14.22.15.33-.02.04-.01.12-.02.2z"/></svg>
              Telegram
            </h2>
            <p className="text-sm text-base-content/70">
              Trigger agents using Telegram bots. Coming soon in a future update!
            </p>
            <div className="mt-auto pt-4 flex justify-between items-center">
                <span className="badge badge-outline">Beta</span>
                <button className="btn btn-disabled btn-sm">Not Configured</button>
            </div>
          </div>
        </div>
        
        {/* Discord Card (Coming Soon) */}
        <div className="glass-card opacity-60 grayscale hover:grayscale-0 transition-all duration-300">
          <div className="h-2 bg-[#5865F2] w-full"></div>
          <div className="card-body">
            <h2 className="card-title flex items-center gap-2 mb-2 text-2xl">
              <Server className="text-[#5865F2] w-6 h-6" />
              Discord
            </h2>
            <p className="text-sm text-base-content/70">
              Use Slash commands to run your agents directly in your server channels. Coming soon!
            </p>
            <div className="mt-auto pt-4 flex justify-between items-center">
                <span className="badge badge-outline">Beta</span>
                <button className="btn btn-disabled btn-sm">Not Configured</button>
            </div>
          </div>
        </div>

      </div>

    </div>
  );
}
