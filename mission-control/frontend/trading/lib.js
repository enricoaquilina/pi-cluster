import { h, render, Component } from 'preact';
import { useState, useEffect, useRef, useCallback, useMemo } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

export { html, h, render, Component, useState, useEffect, useRef, useCallback, useMemo };

export function formatUsd(val) {
  if (val == null || isNaN(val)) return '$0.00';
  const sign = val < 0 ? '-' : val > 0 ? '+' : '';
  return `${sign}$${Math.abs(val).toFixed(2)}`;
}

export function formatPct(val) {
  if (val == null || isNaN(val)) return '0%';
  return `${val.toFixed(1)}%`;
}

export function formatTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

export function formatDate(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString('en-GB', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function pnlClass(val) {
  if (val > 0) return 'text-green';
  if (val < 0) return 'text-red';
  return 'text-muted';
}

export function timeAgo(seconds) {
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}
