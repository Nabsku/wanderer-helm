apiVersion: v1
kind: Service
metadata:
  name: {{ include "wanderer.searchServiceName" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: search
  {{- with .Values.search.service.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  type: ClusterIP
  ports:
    - name: http
      port: {{ .Values.search.service.port }}
      targetPort: http
      protocol: TCP
  selector:
    {{- include "wanderer.selectorLabels" . | nindent 4 }}
    app.kubernetes.io/component: search
