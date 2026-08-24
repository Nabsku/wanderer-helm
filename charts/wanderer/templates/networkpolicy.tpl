{{- if .Values.networkPolicy.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ include "wanderer.fullname" . }}-web
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: web
spec:
  podSelector:
    matchLabels:
      {{- include "wanderer.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: web
  policyTypes:
    - Ingress
    - Egress
  {{- if .Values.networkPolicy.webIngress }}
  ingress:
    {{- toYaml .Values.networkPolicy.webIngress | nindent 4 }}
  {{- else }}
  ingress: []
  {{- end }}
  egress:
    - to:
        - podSelector:
            matchLabels:
              {{- include "wanderer.selectorLabels" . | nindent 14 }}
              app.kubernetes.io/component: database
      ports:
        - protocol: TCP
          port: {{ .Values.database.service.port }}
    - to:
        - podSelector:
            matchLabels:
              {{- include "wanderer.selectorLabels" . | nindent 14 }}
              app.kubernetes.io/component: search
      ports:
        - protocol: TCP
          port: {{ .Values.search.service.port }}
    - to:
        - namespaceSelector: {}
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    {{- with .Values.networkPolicy.webEgress }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ include "wanderer.fullname" . }}-database
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: database
spec:
  podSelector:
    matchLabels:
      {{- include "wanderer.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: database
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              {{- include "wanderer.selectorLabels" . | nindent 14 }}
              app.kubernetes.io/component: web
      ports:
        - protocol: TCP
          port: {{ .Values.database.service.port }}
  egress:
    - to:
        - podSelector:
            matchLabels:
              {{- include "wanderer.selectorLabels" . | nindent 14 }}
              app.kubernetes.io/component: search
      ports:
        - protocol: TCP
          port: {{ .Values.search.service.port }}
    - to:
        - namespaceSelector: {}
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    {{- with .Values.networkPolicy.databaseEgress }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ include "wanderer.fullname" . }}-search
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: search
spec:
  podSelector:
    matchLabels:
      {{- include "wanderer.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: search
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              {{- include "wanderer.selectorLabels" . | nindent 14 }}
              app.kubernetes.io/component: web
        - podSelector:
            matchLabels:
              {{- include "wanderer.selectorLabels" . | nindent 14 }}
              app.kubernetes.io/component: database
      ports:
        - protocol: TCP
          port: {{ .Values.search.service.port }}
  egress:
    - to:
        - namespaceSelector: {}
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    {{- with .Values.networkPolicy.searchEgress }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
{{- end }}
