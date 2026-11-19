# One-tap YouTube publishing

Send the bot a video, check the summary it shows, tap **Publish public**. The server uploads it to your channel with the title, description and tags. Nothing is uploaded without that tap, and a voice note cannot give it.

## What Google requires (once, about 15 minutes)

Google only lets an app upload to a channel through a project you register. The labels below are from memory of the Google Cloud console and may have moved; the names of the things to create are what matter.

1. Go to console.cloud.google.com, signed in with the Google account that owns the channel. Create a project, for example `jarvis-publish`.
2. **APIs & Services > Library**: search for **YouTube Data API v3** and press **Enable**.
3. **APIs & Services > OAuth consent screen** (also shown as **Google Auth Platform**): choose **External**, give the app a name and your email, and add your own Google account under **Test users**.
4. **APIs & Services > Credentials > Create credentials > OAuth client ID**. Application type: **TVs and Limited Input devices**. Copy the **Client ID** and the **Client secret**.
5. On the server, enter the two values (paste when asked; the secret is not shown):

   ```bash
   read -rp "YouTube client ID: " V && sed -i "s|^YOUTUBE_CLIENT_ID=.*|YOUTUBE_CLIENT_ID=$V|" /etc/jarvis-sidekick/env; unset V
   read -rsp "YouTube client secret: " V && sed -i "s|^YOUTUBE_CLIENT_SECRET=.*|YOUTUBE_CLIENT_SECRET=$V|" /etc/jarvis-sidekick/env; unset V; echo
   systemctl restart jarvis-sidekick
   ```

6. In Telegram send `/youtube`. The bot replies with a web address and a short code. Open the address on any device, enter the code, choose the channel's Google account, and allow access. Google shows a "Google hasn't verified this app" page first, because the project is yours and unreviewed: choose **Advanced**, then continue. The bot tells you when it is connected.

## Publishing a video

1. Send the video to the bot (as a video or a file, up to 20 MB, which is Telegram's limit for bots).
2. Put the text in the caption, or send it as the next message: the title on the first line, the description below, and optionally a last line `Tags: one, two, three`.
3. The bot shows the title, the size and any problem with your publishing rules. An error (title over 60 characters, a missing "Narration: AI-generated voice." line, a blocklisted name, a promise of an outcome) removes the publish buttons until you send corrected text with `/text`.
4. Tap **Publish public**, **Upload private** or **Skip**. The reply has the link and the visibility YouTube applied.

Upload one video as **private** first, to see the whole path work.

## Two things Google does that you should expect

- **Uploads may be forced to Private.** Google's policy, as I recall it, is that videos uploaded through the API by a project that has not passed their audit are locked to private. If that applies, the bot says so in its reply, and you change Visibility to Public in the YouTube app. This could not be confirmed from Google's documentation while writing this; your first public upload will show it.
- **The sign-in may expire after 7 days** while the project's publishing status is **Testing**. The bot then asks you to send `/youtube` again. Setting the status to **In production** on the consent screen page (no review is needed for personal use; the "unverified" page stays) is the usual way to stop that.

## Removing access

`/youtube disconnect` deletes the saved sign-in from the server. To revoke it at Google as well: myaccount.google.com > Security > Third-party access.

## What is stored

A long-lived sign-in token for your channel, in `youtube-token.json` in the bot's state folder, readable only by the bot's account. With it, the server can upload to and manage your channel, which is why uploading is gated behind your tap and why the language model, if you ever enable it, has no tool that touches this.
