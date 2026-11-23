(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const sampleRate = 16000;
  const preRollSamples = sampleRate * 0.32;
  const maxSamples = sampleRate * 30;
  const echoTailMs = 240;
  const state = {
    books: [], ws: null, ready: false, mic: null, micRequest: 0, micPending: false,
    audio: null, worklet: null, sources: new Set(), playTime: 0, playRate: 0, playingFiller: false,
    echoUntil: 0, turn: null, capture: null, pendingAcks: 0, flushId: 0,
    vad: { frames: [], samples: 0, noise: 0.001, speech: 0, gap: 0, lastVoice: 0 },
    lastMeterAt: 0, gain: 8, sensitivity: 3
  };

  const now = () => performance.now();
  const ms = value => Math.round(value) + ' ms';
  const connected = () => state.ws?.readyState === WebSocket.OPEN;
  const autoMode = () => $('mode').value === 'auto';
  const canListen = () => state.ready && state.mic && !state.turn &&
    !state.sources.size && now() >= state.echoUntil;

  function log(text) {
    const row = document.createElement('li');
    row.textContent = `${new Date().toLocaleTimeString()} · ${text}`;
    $('log').prepend(row);
    if ($('log').children.length > 35) $('log').lastElementChild.remove();
  }

  function notice(text = '') {
    $('notice').textContent = text;
    $('notice').hidden = !text;
  }

  function showTranscript(text = '') {
    const preview = $('asrPreview');
    preview.hidden = !text;
    preview.textContent = text ? '识别中 · ' + text : '';
  }

  function render() {
    const recording = Boolean(state.capture);
    const replying = Boolean(state.turn || state.sources.size);
    const awaiting = Boolean(state.ws && !state.ready);
    const busy = recording || replying || awaiting;
    const auto = autoMode();
    const micReady = Boolean(state.mic);
    const ready = state.ready;
    const status = $('status');
    status.dataset.state = recording ? 'recording' : busy ? 'busy' : ready ? 'ready' : 'offline';
    status.textContent = recording ? '正在录音' : state.sources.size ? '正在播放回复' :
      state.turn ? '正在准备回复' : awaiting ? '正在连接' : ready ? '已连接' : '未连接';
    $('connectBtn').disabled = !state.ws && !state.books.length;
    $('connectBtn').textContent = state.ws ? '断开' : '连接';
    $('interruptBtn').hidden = !replying || recording;
    $('book').disabled = busy || !state.books.length;
    $('page').disabled = $('book').disabled;
    $('mode').disabled = recording;
    $('device').disabled = recording || state.micPending;
    $('echo').disabled = $('noise').disabled = $('device').disabled;
    $('micBtn').disabled = !ready || recording || state.micPending;
    $('micBtn').textContent = micReady ? '关闭' : '启用';
    $('holdBtn').hidden = auto;
    $('holdBtn').disabled = !ready || !micReady || Boolean(state.capture?.flush) ||
      (recording && state.capture.kind !== 'push');
    $('holdBtn').dataset.recording = String(state.capture?.kind === 'push');
    $('holdBtn').textContent = state.capture?.kind === 'push' ? '松开结束' : '按住说话';
    $('autoLabel').hidden = !auto;
    $('autoLabel').dataset.listening = String(ready && micReady && !replying);
    $('autoLabel').textContent = recording ? '正在听' : ready && micReady && !replying ? '正在等待你说话' : '自动听取已暂停';
    $('voiceHint').textContent = !ready ? '连接后可使用麦克风' : !micReady ? '启用麦克风，或直接输入文字' :
      auto ? replying ? '回复结束后恢复听取' : '说完停一小会儿即可' : '也可以按住空格键';
    $('sendBtn').disabled = !ready || recording || !$('question').value.trim();
    $('selftestBtn').disabled = !ready || recording || replying;
    for (const button of document.querySelectorAll('.suggestion')) button.disabled = !ready || recording;
  }

  function setPreview() {
    const book = state.books.find(book => book.id === $('book').value);
    if (!book) return;
    const page = $('page');
    page.max = Math.max(1, book.page_count);
    page.value = Math.min(Number(page.max), Math.max(1, Math.trunc(Number(page.value)) || 1));
    $('preview').hidden = false;
    $('preview').alt = `${book.title}第 ${page.value} 页`;
    $('preview').src = `/api/books/${encodeURIComponent(book.id)}/pages/${page.value}`;
    $('previewEmpty').textContent = '此页暂无预览';
  }

  async function loadBooks() {
    try {
      const response = await fetch('/api/books');
      if (!response.ok) throw new Error(response.status);
      state.books = (await response.json()).books;
      $('book').replaceChildren(...state.books.map(book => new Option(book.title, book.id)));
      if (!state.books.length) {
        $('book').add(new Option('暂无绘本'));
        notice('服务里还没有绘本。导入绘本后刷新页面。');
      }
      setPreview();
    } catch (error) {
      $('book').replaceChildren(new Option('加载失败'));
      notice('无法获取绘本列表，请刷新后重试。');
      log(`读取绘本失败 · ${error.message}`);
    }
    render();
  }

  function send(message) {
    if (!connected()) return false;
    state.ws.send(JSON.stringify(message));
    return true;
  }

  function sendContext() {
    state.ready = false;
    resetVad();
    send({
      type: 'start', book_id: $('book').value, page: Number($('page').value),
      sample_rate: sampleRate, vad: 'client', warmup: false
    });
    render();
  }

  async function ensureAudio() {
    if (!state.audio) {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) throw new Error('浏览器不支持音频播放');
      state.audio = new Audio();
    }
    const audio = state.audio;
    if (audio.state !== 'running') await audio.resume();
    return audio;
  }

  function stopPlayback() {
    for (const source of state.sources) {
      source.onended = null;
      source.stop();
      source.disconnect();
    }
    if (state.sources.size) state.echoUntil = now() + echoTailMs;
    state.sources.clear();
    state.playTime = 0;
    state.playRate = 0;
    state.playingFiller = false;
    resetVad();
  }

  function playAudio(bytes) {
    const audio = state.audio;
    const rate = state.playRate;
    const length = Math.floor(bytes.byteLength / 2);
    if (!audio || !rate || !length) return;
    const buffer = audio.createBuffer(1, length, rate);
    const view = new DataView(bytes);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < length; i++) channel[i] = view.getInt16(i * 2, true) / 32768;
    const source = audio.createBufferSource();
    source.buffer = buffer;
    source.connect(audio.destination);
    const at = Math.max(state.playTime, audio.currentTime + 0.015);
    source.start(at);
    state.playTime = at + buffer.duration;
    state.sources.add(source);
    source.onended = () => {
      source.disconnect();
      state.sources.delete(source);
      if (!state.sources.size) {
        state.echoUntil = now() + echoTailMs;
        resetVad();
        render();
      }
    };
    if (state.turn && !state.turn.firstAudio && !state.playingFiller && audio.state === 'running') {
      const delay = now() - state.turn.started + (at - audio.currentTime) * 1000;
      state.turn.firstAudio = true;
      $('latency').hidden = false;
      $('latency').dataset.slow = String(delay > 2000);
      $('latency').textContent = `首音 ${ms(delay)}`;
    }
    if (state.sources.size === 1) render();
  }

  function followThread() {
    const thread = $('thread');
    if (thread.scrollHeight - thread.scrollTop - thread.clientHeight < 120) {
      thread.scrollTop = thread.scrollHeight;
    }
  }

  function message(role, text) {
    $('welcome').hidden = true;
    const article = document.createElement('article');
    article.className = `message ${role}`;
    const author = document.createElement('span');
    author.className = 'message-author';
    author.textContent = role === 'user' ? '你' : '纸书智伴';
    const paragraph = document.createElement('p');
    paragraph.className = 'message-text';
    paragraph.textContent = text;
    article.append(author, paragraph);
    $('messages').append(article);
    followThread();
    return paragraph;
  }

  function finishTranscript(note) {
    const draft = state.turn?.draft;
    if (!draft) return;
    if (!draft.textContent) draft.parentElement.remove();
    else if (note) {
      const meta = document.createElement('div');
      meta.className = 'message-note';
      meta.textContent = note;
      draft.after(meta);
    }
  }

  function newTurn(started, fromVoice = false) {
    state.turn = { started, fromVoice, firstAudio: false, draft: null, text: '', answered: false };
    $('latency').hidden = true;
    $('asrMs').textContent = '—';
    $('asrFirst').textContent = '—';
    $('serverLatency').textContent = '—';
    notice();
    render();
  }

  function interrupt() {
    if (state.turn && !state.turn.answered) finishTranscript('已打断');
    state.turn = null;
    showTranscript();
    stopPlayback();
    if (send({ type: 'barge_in' })) {
      state.pendingAcks++;
      setTimeout(() => { state.pendingAcks = 0; }, 600);
    }
    render();
  }

  function handleEvent(event) {
    if (event.type === 'barge_in_ack') {
      state.pendingAcks = Math.max(0, state.pendingAcks - 1);
      return;
    }
    // 注意：不能用 pendingAcks 拦掉全部事件——ack 若丢失/迟到会把
    // asr_final/answer/done 一起吞掉，界面就永远停在“正在准备回复”。
    if (event.type === 'ready') {
      state.ready = true;
      log(`已连接 · ${event.book_id} · 第 ${$('page').value} 页`);
      render();
      return;
    }
    if (event.type === 'asr_partial') {
      if (state.capture || state.turn?.fromVoice) {
        showTranscript(event.text);
        if (event.ms != null && $('asrFirst').textContent === '—') $('asrFirst').textContent = ms(event.ms);
      }
      return;
    }
    if (event.type === 'error') {
      showTranscript();
      log(`回复失败 · ${event.stage || '连接'} · ${event.message}`);
      notice(state.ready ? '这次回复失败了，可以重新问一遍。' : '无法建立对话，请断开后重试。');
      return;
    }
    const turn = state.turn;
    if (!turn) return;
    switch (event.type) {
      case 'speech_end':
        $('duration').textContent = `${(event.bytes / (sampleRate * 2)).toFixed(2)} 秒`;
        log(`收到录音 · ${event.bytes} 字节`);
        break;
      case 'turn_skipped':
        notice('刚才录到的声音太短。请说完一句话再松开。');
        log('录音短于 100 ms');
        break;
      case 'asr_final':
        showTranscript();
        $('asrMs').textContent = ms(event.ms);
        if (event.first_text_ms != null) $('asrFirst').textContent = ms(event.first_text_ms);
        if (event.text) message('user', event.text);
        else log('语音识别未返回文字');
        break;
      case 'answer_delta':
        turn.text += event.text || '';
        if (turn.draft) turn.draft.textContent = turn.text;
        else if (turn.text) turn.draft = message('assistant', turn.text);
        followThread();
        break;
      case 'audio_start':
        state.playRate = event.sample_rate || 24000;
        state.playingFiller = Boolean(event.filler);
        break;
      case 'audio_end':
        state.playRate = 0;
        state.playingFiller = false;
        break;
      case 'answer': {
        const text = event.text ?? turn.text;
        if (turn.draft) turn.draft.textContent = text;
        else if (text) turn.draft = message('assistant', text);
        const first = event.metrics?.first_reply_audio_after_speech_ms;
        if (first != null) $('serverLatency').textContent = ms(first);
        turn.answered = true;
        followThread();
        break;
      }
      case 'tts_error':
        notice('语音播放暂不可用，可以继续阅读文字回复。');
        log(`语音合成失败 · ${event.message}`);
        break;
      case 'done':
        showTranscript();
        if (!turn.answered) finishTranscript('回复未完成');
        state.turn = null;
        resetVad();
        render();
        break;
    }
  }

  function disconnect(reason) {
    const socket = state.ws;
    state.ws = null;
    state.ready = false;
    state.pendingAcks = 0;
    showTranscript();
    space = false;
    pointer = null;
    if (state.turn && !state.turn.answered) finishTranscript('连接已断开');
    state.turn = null;
    clearTimeout(state.capture?.flushTimer);
    state.capture = null;
    stopMic();
    stopPlayback();
    if (socket) socket.close();
    if (state.audio) state.audio.close().catch(() => {});
    state.audio = state.worklet = null;
    notice(reason);
    render();
  }

  function connect() {
    notice();
    ensureAudio().catch(error => log(`音频输出初始化失败 · ${error.message}`));
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const id = crypto.randomUUID?.() || Math.random().toString(36).slice(2);
    const socket = new WebSocket(`${protocol}//${location.host}/ws/voice/web-${id}`);
    state.ws = socket;
    socket.binaryType = 'arraybuffer';
    socket.onopen = () => { if (state.ws === socket) sendContext(); };
    socket.onmessage = ({ data }) => {
      if (state.ws !== socket) return;
      if (data instanceof ArrayBuffer) {
        if (state.turn && !state.pendingAcks) playAudio(data);
      } else {
        handleEvent(JSON.parse(data));
      }
    };
    socket.onclose = () => {
      if (state.ws === socket) disconnect('与服务的连接已断开，请重新连接。');
    };
    socket.onerror = () => log('实时连接失败');
    startMic();
    render();
  }

  function resetVad() {
    const vad = state.vad;
    vad.frames = [];
    vad.samples = vad.speech = vad.gap = vad.lastVoice = 0;
  }

  function beginCapture(kind) {
    if (!state.ready || state.capture) return false;
    if (state.turn || state.sources.size) interrupt();
    $('latency').hidden = true;
    $('duration').textContent = '—';
    showTranscript();
    notice();
    state.capture = { kind, samples: 0, flush: 0, flushTimer: 0 };
    send({ type: 'vad', event: 'speech_start' });
    render();
    return true;
  }

  function sendFrame(pcm) {
    if (!connected() || !state.capture) return;
    state.ws.send(pcm);
    state.capture.samples += pcm.byteLength / 2;
  }

  function endCapture(ended = now()) {
    const capture = state.capture;
    if (!capture) return;
    clearTimeout(capture.flushTimer);
    state.capture = null;
    resetVad();
    if (!state.ready) { render(); return; }
    newTurn(ended, true);
    send({ type: 'vad', event: 'speech_end', t: Math.round(Date.now() - (now() - ended)) });
    const turnRef = state.turn;
    setTimeout(() => {
      if (state.turn === turnRef && !turnRef.answered && !turnRef.text) {
        finishTranscript('响应超时');
        state.turn = null;
        notice('这次没有收到回复，请再问一遍。');
        log('回合看门狗超时（12s 无 ASR/回答）');
        render();
      }
    }, 12000);
  }

  function flushPush() {
    const capture = state.capture;
    if (capture?.kind !== 'push' || capture.flush) return;
    if (!state.mic) { endCapture(); return; }
    capture.released = now();
    capture.flush = ++state.flushId;
    state.mic.node.port.postMessage({ type: 'flush', id: capture.flush });
    capture.flushTimer = setTimeout(() => {
      if (state.capture === capture) endCapture(capture.released);
    }, 250);
    render();
  }

  function processFrame(samples) {
    const pcm = new ArrayBuffer(samples.length * 2);
    const view = new DataView(pcm);
    let power = 0;
    let rawPower = 0;
    let clipped = false;
    for (let index = 0; index < samples.length; index++) {
      const raw = samples[index];
      const amplified = raw * state.gain;
      const sample = Math.max(-1, Math.min(1, amplified));
      power += sample * sample;
      rawPower += raw * raw;
      clipped ||= Math.abs(amplified) > 1;
      view.setInt16(index * 2, sample * (sample < 0 ? 32768 : 32767), true);
    }
    const received = now();
    const rms = Math.sqrt(power / samples.length);
    if (received - state.lastMeterAt >= 100) {
      state.lastMeterAt = received;
      $('meterFill').style.width = Math.min(100, rms * 500) + '%';
      const db = 20 * Math.log10(Math.max(0.00001, Math.sqrt(rawPower / samples.length)));
      $('inputLevel').textContent = `${Math.round(db)} dBFS${clipped ? ' · 增益过高' : ''}`;
    }
    const capture = state.capture;
    if (capture?.kind === 'push') {
      sendFrame(pcm);
      if (capture.samples >= maxSamples) endCapture(received);
      return;
    }
    if (!autoMode() || !canListen() || capture?.kind === 'sample') return;

    const vad = state.vad;
    const frameMs = samples.length * 1000 / sampleRate;
    const threshold = Math.max(0.014 / state.sensitivity,
      Math.min(0.05, vad.noise * state.gain * (4.4 - state.sensitivity * 0.28)));
    const voiced = rms >= threshold;
    if (capture?.kind === 'auto') {
      sendFrame(pcm);
      if (voiced) vad.lastVoice = received;
      vad.gap = voiced ? 0 : vad.gap + frameMs;
      if (vad.gap >= 260 || capture.samples >= maxSamples) endCapture(vad.lastVoice || received);
      return;
    }
    vad.frames.push(pcm);
    vad.samples += samples.length;
    while (vad.samples > preRollSamples) vad.samples -= vad.frames.shift().byteLength / 2;
    if (voiced) {
      vad.speech += frameMs;
      vad.gap = 0;
      vad.lastVoice = received;
      if (vad.speech >= 100 && beginCapture('auto')) {
        for (const frame of vad.frames) sendFrame(frame);
        vad.frames = [];
        vad.samples = 0;
      }
    } else {
      vad.gap += frameMs;
      if (vad.gap > 40) {
        vad.speech = 0;
        vad.noise = vad.noise * 0.97 + (rms / state.gain) * 0.03;
      }
    }
  }

  async function listDevices(selected, request) {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      if (state.micRequest !== request) return;
      const inputs = devices.filter(device => device.kind === 'audioinput' && device.deviceId !== 'default' && device.deviceId !== 'communications');
      const select = $('device');
      select.replaceChildren(new Option('系统默认', ''), ...inputs.map((device, index) =>
        new Option(device.label || `麦克风 ${index + 1}`, device.deviceId)));
      select.value = inputs.some(input => input.deviceId === selected) ? selected : '';
    } catch {
      log('无法列出麦克风设备');
    }
  }

  function stopMic() {
    state.micRequest++;
    state.micPending = false;
    const mic = state.mic;
    state.mic = null;
    if (mic) {
      mic.node.port.onmessage = null;
      mic.node.port.close();
      mic.source.disconnect();
      mic.node.disconnect();
      mic.stream.getTracks().forEach(track => track.stop());
    }
    resetVad();
    state.vad.noise = 0.001;
    $('meterFill').style.width = '0';
    $('inputLevel').textContent = '—';
    $('micInfo').textContent = '麦克风尚未开启';
  }

  async function startMic() {
    if (!state.ws || state.capture) return;
    stopMic();
    const request = state.micRequest;
    state.micPending = true;
    $('micInfo').textContent = '正在打开麦克风…';
    render();
    let stream;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('请用 localhost 或 HTTPS 打开此页。');
      const audio = await ensureAudio();
      if (state.micRequest !== request) return;
      if (!audio.audioWorklet) throw new Error('此浏览器不支持实时录音，请用 Chrome 或 Edge。');
      if (!state.worklet) {
        const load = audio.audioWorklet.addModule('/test/assets/capture-worklet.js');
        state.worklet = load;
        load.catch(() => { if (state.worklet === load) state.worklet = null; });
      }
      const worklet = state.worklet;
      const device = $('device').value;
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1, deviceId: device ? { exact: device } : undefined,
          echoCancellation: $('echo').checked, noiseSuppression: $('noise').checked, autoGainControl: false
        }
      });
      await worklet;
      if (state.micRequest !== request) return;
      const source = audio.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(audio, 'paperbook-capture', { numberOfInputs: 1, numberOfOutputs: 0, channelCount: 1, channelCountMode: 'explicit' });
      node.port.onmessage = ({ data }) => {
        if (state.mic?.node !== node) return;
        if (data.samples?.length) processFrame(data.samples);
        if (data.flushed && state.capture?.flush === data.flushed) endCapture(state.capture.released);
      };
      state.mic = { stream, source, node };
      source.connect(node);
      const track = stream.getAudioTracks()[0];
      const captureFailed = () => {
        if (state.mic?.node !== node) return;
        if (state.capture && state.capture.kind !== 'sample') endCapture();
        stopMic();
        $('micInfo').textContent = '麦克风已断开，请重新启用。';
        render();
      };
      track.addEventListener('ended', captureFailed, { once: true });
      node.addEventListener('processorerror', captureFailed, { once: true });
      $('micInfo').textContent = track.label || '麦克风已启用';
      log(`麦克风已启用 · ${audio.sampleRate} Hz → 16 kHz`);
      listDevices(device, request);
    } catch (error) {
      if (state.micRequest !== request) return;
      if (state.mic) stopMic();
      const messages = {
        NotAllowedError: '麦克风权限未开启。允许权限后点击启用。',
        NotFoundError: '没有找到麦克风，请检查输入设备。',
        NotReadableError: '麦克风暂不可用，请检查是否被其他应用占用。',
        OverconstrainedError: '所选设备不可用，请切换到系统默认。'
      };
      $('micInfo').textContent = messages[error.name] || error.message || '无法打开麦克风。';
      log(`麦克风开启失败 · ${error.name}`);
    } finally {
      if (stream && state.mic?.stream !== stream) stream.getTracks().forEach(track => track.stop());
      if (state.micRequest === request) state.micPending = false;
      render();
    }
  }

  function sendText(text) {
    if (!text || !state.ready || state.capture) return false;
    ensureAudio().catch(error => log(error.message));
    if (state.turn || state.sources.size) interrupt();
    newTurn(now());
    message('user', text);
    return send({ type: 'text', text, t: Date.now() });
  }

  async function runSample() {
    if (!beginCapture('sample')) return;
    const capture = state.capture;
    try {
      const response = await fetch(`/api/books/${encodeURIComponent($('book').value)}/queries/06.wav`);
      if (!response.ok) throw new Error('此绘本暂无测试音频。');
      const bytes = await response.arrayBuffer();
      if (state.capture !== capture) return;
      const audio = await ensureAudio();
      const decoded = await audio.decodeAudioData(bytes);
      if (state.capture !== capture) return;
      const resampler = new OfflineAudioContext(1, Math.ceil(decoded.duration * sampleRate), sampleRate);
      const source = resampler.createBufferSource();
      source.buffer = decoded;
      source.connect(resampler.destination);
      source.start();
      const samples = (await resampler.startRendering()).getChannelData(0);
      if (state.capture !== capture) return;
      const pcm = new DataView(new ArrayBuffer(Math.min(samples.length, maxSamples) * 2));
      for (let i = 0; i < pcm.byteLength / 2; i++) pcm.setInt16(i * 2, Math.min(32767, Math.max(-32768, samples[i] * 32768)), true);
      for (let offset = 0; offset < pcm.byteLength; offset += 6400) sendFrame(pcm.buffer.slice(offset, offset + 6400));
      endCapture();
    } catch (error) {
      if (state.capture !== capture) return;
      state.capture = null;
      interrupt();
      notice(error.message || '无法播放测试音频。');
    }
  }

  $('connectBtn').onclick = () => state.ws ? disconnect('') : connect();
  $('interruptBtn').onclick = interrupt;
  $('micBtn').onclick = () => { if (state.mic) { stopMic(); render(); } else startMic(); };
  for (const id of ['device', 'echo', 'noise']) $(id).onchange = () => { if (state.mic) startMic(); };
  $('mode').onchange = () => { resetVad(); render(); };
  $('book').onchange = () => { $('page').value = 1; setPreview(); if (connected()) sendContext(); };
  $('page').onchange = () => { setPreview(); if (connected()) sendContext(); };
  $('preview').onerror = () => { $('preview').hidden = true; };
  $('gain').oninput = () => { state.gain = Number($('gain').value); $('gainValue').value = `${state.gain}×`; };
  $('sensitivity').oninput = () => {
    state.sensitivity = Number($('sensitivity').value);
    $('sensitivityValue').value = String(state.sensitivity);
  };
  $('question').oninput = render;
  $('composerForm').onsubmit = event => {
    event.preventDefault();
    if (sendText($('question').value.trim())) $('question').value = '';
    render();
  };
  for (const button of document.querySelectorAll('.suggestion')) button.onclick = () => sendText(button.textContent.trim());
  $('selftestBtn').onclick = runSample;

  const hold = $('holdBtn');
  let pointer = null;
  let space = false;
  function startPush() {
    if (!state.mic || autoMode()) return false;
    ensureAudio().catch(error => log(error.message));
    return beginCapture('push');
  }
  hold.addEventListener('pointerdown', event => {
    if (event.button !== 0 || space || pointer !== null || !startPush()) return;
    event.preventDefault();
    pointer = event.pointerId;
    hold.setPointerCapture(pointer);
  });
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) hold.addEventListener(type, event => {
    if (event.pointerId !== pointer) return;
    pointer = null;
    flushPush();
  });
  hold.addEventListener('click', event => {
    if (event.detail !== 0) return;
    if (state.capture?.kind === 'push') flushPush();
    else startPush();
  });
  window.addEventListener('keydown', event => {
    const interactive = event.target.closest?.('input, textarea, select, button, summary, [contenteditable]');
    if (event.code !== 'Space' || event.repeat || pointer !== null || (interactive && interactive !== hold)) return;
    if (startPush()) { space = true; event.preventDefault(); }
  });
  window.addEventListener('keyup', event => {
    if (event.code !== 'Space' || !space) return;
    space = false;
    event.preventDefault();
    flushPush();
  });
  window.addEventListener('blur', () => { space = false; pointer = null; flushPush(); });
  window.addEventListener('pagehide', () => { if (state.ws) disconnect(''); });
  loadBooks();
})();
