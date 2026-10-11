'use strict';
/**
 * MMA Oracle - Firebase Integration Module
 * Project: mma-oracle (196295291462)
 * Web App: MMA Oracle Predictions (1:196295291462:web:41461eac227e7aee571047)
 */
const firebaseConfig = {
  projectId: "mma-oracle",
  appId: "1:196295291462:web:41461eac227e7aee571047",
  storageBucket: "mma-oracle.firebasestorage.app",
  apiKey: "AIzaSyB3aAAAiH8pszj3QBQ78ft-52LtbSSlhu8",
  authDomain: "mma-oracle.firebaseapp.com",
  messagingSenderId: "196295291462",
  measurementId: "G-WFKM9M8TFN"
};

let firebaseApp = null;
let firebaseAnalytics = null;

(function initFirebase() {
  if (typeof firebase === 'undefined') {
    // Graceful fallback if offline or CDN is unreachable
    window.mmaFirebase = { connected: false, config: firebaseConfig };
    return;
  }
  try {
    if (!firebase.apps || !firebase.apps.length) {
      firebaseApp = firebase.initializeApp(firebaseConfig);
    } else {
      firebaseApp = firebase.app();
    }
    if (typeof firebase.analytics === 'function' && firebaseConfig.measurementId) {
      firebaseAnalytics = firebase.analytics();
    }
    window.mmaFirebase = {
      app: firebaseApp,
      analytics: firebaseAnalytics,
      config: firebaseConfig,
      connected: true
    };
    console.info('[MMA Cipher] Connected to Firebase project: mma-oracle');
  } catch (err) {
    console.warn('[MMA Cipher] Firebase initialization error:', err);
    window.mmaFirebase = { connected: false, error: err, config: firebaseConfig };
  }
})();

