# Making this PC reachable for WhatsApp

WhatsApp (Meta) needs to reach this PC over the internet to deliver customer replies and message status updates. Follow these steps once:

1. Get a domain name, or a free dynamic-DNS name (for example from DuckDNS or No-IP) that points to your internet connection.
2. Log in to your router and forward TCP port 443 to this PC.
3. Install an HTTPS reverse proxy such as Caddy on this PC and point it at ServiceBills on port 8000. Caddy gets a free HTTPS certificate for you automatically.
4. Paste your https address (for example `https://billing.example.com`) into the Public URL field and save.
5. Copy the Webhook URL and Verify Token shown on this page into Meta's WhatsApp configuration, then click Verify and Save.

This is the same text as the in-app help dialog (`frontend/src/components/WhatsAppPublicUrlHelp.js`); keep the two in sync.
