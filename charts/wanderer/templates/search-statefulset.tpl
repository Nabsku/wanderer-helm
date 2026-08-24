apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {{ include "wanderer.searchServiceName" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: search
spec:
  serviceName: {{ include "wanderer.searchServiceName" . }}
  replicas: {{ .Values.search.replicaCount }}
  podManagementPolicy: OrderedReady
  updateStrategy:
    type: RollingUpdate
  selector:
    matchLabels:
      {{- include "wanderer.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: search
  template:
    metadata:
      annotations:
        checksum/wanderer-secret: {{ include "wanderer.secretChecksum" . | quote }}
        {{- with .Values.search.podAnnotations }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      labels:
        {{- include "wanderer.labels" . | nindent 8 }}
        app.kubernetes.io/component: search
        {{- with .Values.search.podLabels }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
    spec:
      serviceAccountName: {{ include "wanderer.serviceAccountName" . }}
      automountServiceAccountToken: {{ .Values.serviceAccount.automountServiceAccountToken }}
      terminationGracePeriodSeconds: {{ .Values.search.terminationGracePeriodSeconds }}
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.search.podSecurityContext }}
      securityContext:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.search.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.search.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.search.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.search.topologySpreadConstraints }}
      topologySpreadConstraints:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: search
          image: {{ include "wanderer.image" .Values.search.image | quote }}
          imagePullPolicy: {{ .Values.search.image.pullPolicy }}
          securityContext:
            {{- toYaml .Values.search.securityContext | nindent 12 }}
          ports:
            - name: http
              containerPort: 7700
              protocol: TCP
          env:
            - name: MEILI_ENV
              value: {{ .Values.search.config.environment | quote }}
            - name: MEILI_NO_ANALYTICS
              value: {{ .Values.search.config.noAnalytics | quote }}
            - name: MEILI_MASTER_KEY
              valueFrom:
                secretKeyRef:
                  name: {{ include "wanderer.secretName" . }}
                  key: {{ .Values.secret.keys.meiliMaster }}
            {{- with .Values.search.extraEnv }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          {{- with .Values.search.extraEnvFrom }}
          envFrom:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          volumeMounts:
            - name: search-data
              mountPath: /meili_data
            - name: tmp
              mountPath: /tmp
          {{- with .Values.search.startupProbe }}
          {{- if .enabled }}
          startupProbe:
            httpGet:
              path: {{ .path }}
              port: http
            periodSeconds: {{ .periodSeconds }}
            timeoutSeconds: {{ .timeoutSeconds }}
            failureThreshold: {{ .failureThreshold }}
          {{- end }}
          {{- end }}
          {{- with .Values.search.livenessProbe }}
          {{- if .enabled }}
          livenessProbe:
            httpGet:
              path: {{ .path }}
              port: http
            periodSeconds: {{ .periodSeconds }}
            timeoutSeconds: {{ .timeoutSeconds }}
            failureThreshold: {{ .failureThreshold }}
          {{- end }}
          {{- end }}
          {{- with .Values.search.readinessProbe }}
          {{- if .enabled }}
          readinessProbe:
            httpGet:
              path: {{ .path }}
              port: http
            periodSeconds: {{ .periodSeconds }}
            timeoutSeconds: {{ .timeoutSeconds }}
            failureThreshold: {{ .failureThreshold }}
          {{- end }}
          {{- end }}
          resources:
            {{- toYaml .Values.search.resources | nindent 12 }}
      volumes:
        - name: search-data
          {{- if .Values.search.persistence.enabled }}
          persistentVolumeClaim:
            claimName: {{ default (printf "%s-search-data" (include "wanderer.fullname" .)) .Values.search.persistence.existingClaim }}
          {{- else }}
          emptyDir: {}
          {{- end }}
        - name: tmp
          emptyDir: {}
