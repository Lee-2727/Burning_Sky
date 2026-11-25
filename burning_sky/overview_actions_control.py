--- a/burning_sky/overview_actions_control.py
+++ b/burning_sky/overview_actions_control.py
@@ -48,7 +48,7 @@
         if not token:
             raise ValueError('缺少 GitHub Actions Token')
         self.base = f'https://api.github.com/repos/{repo}'
-        self.branch = branch
+        self.branch = branch.strip()
         self.request = request
         self.headers = {
             'Authorization': f'Bearer {token}',
@@ -59,8 +59,24 @@
     def api(self, method, path, **kwargs):
         response = self.request(method, self.base + path, headers=self.headers, timeout=12, **kwargs)
         if not response.ok:
-            # Do not leak request headers or PAT in UI exceptions.
-            raise RuntimeError(f'GitHub API HTTP {response.status_code}，请检查 Actions 权限及工作流状态')
+            try:
+                payload = response.json()
+            except ValueError:
+                payload = {}
+            parts = []
+            if isinstance(payload, dict):
+                if isinstance(payload.get('message'), str):
+                    parts.append(payload['message'])
+                for error in payload.get('errors') or []:
+                    if isinstance(error, str):
+                        parts.append(error)
+                    elif isinstance(error, dict) and isinstance(error.get('message'), str):
+                        parts.append(error['message'])
+            token = self.headers['Authorization'].removeprefix('Bearer ')
+            detail = ' '.join(' '.join(parts).split()).replace(token, '[已隐藏]')[:400]
+            context = f'（{WORKFLOW}，分支 {self.branch}）' if path.endswith('/dispatches') else ''
+            raise RuntimeError(f'GitHub API HTTP {response.status_code}{context}：'
+                               f'{detail or "GitHub 未提供详细原因"}')
         return response.json() if response.content else {}
 
     def workflow_exists(self):
