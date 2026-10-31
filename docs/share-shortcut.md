# Add a source from your phone's share sheet

The shortcut sends the shared URL to `POST <BASE_URL>/api/sources` with your API token. The server finds the feed (or sitemap) and subscribes. The shortcut shows the reply, for example "Subscribed to Netflix TechBlog" or "No feed found".

You need `BASE_URL` and `API_TOKEN` from `/etc/budgie/budgie.env` on the mini PC. The phone must be on your tailnet: the server is tailnet-only.

## iPhone / iPad (Shortcuts app)

1. Open **Shortcuts** → **+** (new shortcut). Name it "Add to newsletter".
2. Tap the **ⓘ** (details) button. Turn on **Show in Share Sheet**. Set the input type to **URLs** only.
3. Add the action **Get Contents of URL**:
   - URL: `<BASE_URL>/api/sources`
   - Method: **POST**
   - Headers: add `Authorization` = `Bearer <API_TOKEN>`
   - Request Body: **JSON**. Add a field: key `url`, type Text, value **Shortcut Input**.
4. Add the action **Get Dictionary Value**: get the value for key `message` in **Contents of URL**.
5. Add the action **Show Result** (or **Show Notification**) with **Dictionary Value**.
6. Test: in Safari, open a blog, tap **Share** → **Add to newsletter**.

## Android ("HTTP Shortcuts" app)

1. Install **HTTP Shortcuts** (by Waboodoo) from Google Play.
2. Create a shortcut → **Regular Shortcut**. Name it "Add to newsletter".
3. **Basic request settings**: Method **POST**, URL `<BASE_URL>/api/sources`.
4. **Request headers**: `Authorization` = `Bearer <API_TOKEN>`.
5. **Request body**: type **Custom text**, content type `application/json`, body:

   ```json
   {"url": "{shared_url}"}
   ```

   Create the variable `shared_url` first (Variables → **+** → **Static variable**, or **Shared text**), and turn on **Allow receiving value from Share dialog**.
6. **Response handling**: show the response in a **toast** or **dialog**. The response body is JSON with a `message` field.
7. Test: in Chrome, open a blog, tap **Share** → **HTTP Shortcuts** → "Add to newsletter".

## Keep the token safe

The token can add and remove sources. If a phone is lost, set a new `API_TOKEN` in `/etc/budgie/budgie.env` and run `sudo systemctl restart budgie-web`.
