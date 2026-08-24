apiVersion: v1
kind: Service
metadata:
  name: {{ include "wanderer.webServiceName" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: web
  {{- with .Values.web.service.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  type: {{ .Values.web.service.type }}
  ports:
    - name: http
      port: {{ .Values.web.service.port }}
      targetPort: http
      protocol: TCP
  selector:
    {{- include "wanderer.selectorLabels" . | nindent 4 }}
    app.kubernetes.io/component: web
