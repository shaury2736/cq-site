/**
 * CQ - Cinema Quotient Frontend JavaScript App
 * Handles dynamic API integration, authentication (Google Sign-In & Email),
 * instant ratings/reviews, watchlist management, trailer modal, and Admin Portal.
 */

// Global User State
let currentUser = null;

// ---------------------------------------------------------------------------
// General Utilities & UI Components
// ---------------------------------------------------------------------------

function getStarsHtml(rating) {
  const full = Math.floor(rating);
  const half = (rating - full) >= 0.5 ? 1 : 0;
  const empty = 5 - full - half;
  
  let html = '';
  for (let i = 0; i < full; i++) html += '★';
  if (half) html += '½';
  for (let i = 0; i < empty; i++) html += '☆';
  return html;
}

function createMovieCard(movie, opts = {}) {
  const rating = movie.vote_average ?? movie.imdb_rating;
  const matchPct = movie.match_percentage || (opts.why ? 85 : null);
  const stars = rating == null
    ? 'Not rated'
    : `${getStarsHtml(rating / 2)} <small>${movie.vote_average != null ? 'TMDB' : 'IMDb'}</small>`;
  const inWatchlist = movie.in_watchlist;
  const whyText = movie.explanation || (opts.why ? `Recommended match for your taste.` : null);
  
  const displayYear = movie.year || (movie.release_date ? movie.release_date.split('-')[0] : 'Year');
  const displayGenre = (movie.genres && movie.genres.length > 0) ? movie.genres[0] : 'Drama';
  
  return `
    <div class="movie-card" data-id="${movie.id}">
      <div class="poster-wrap">
        <img class="poster-img" src="${movie.poster_url || ''}" alt="${movie.title}" loading="lazy" />
        <div class="poster-overlay">
          <div class="top-badges">
            ${matchPct ? `<span class="match-badge-sm">${matchPct}% Match</span>` : ''}
            <button class="watchlist-btn ${inWatchlist ? 'active' : ''}" onclick="toggleWatchlist(${movie.id}, event)" title="${inWatchlist ? 'Remove from Watchlist' : 'Add to Watchlist'}">
              ${inWatchlist ? '✓' : '+'}
            </button>
          </div>
          <div>
            ${movie.user_rating ? `<span class="user-star-badge">You: ${movie.user_rating}★</span>` : ''}
          </div>
        </div>
      </div>
      <div class="card-info">
        <a href="/movie/${movie.id}" class="card-title">${movie.title || 'Unknown Title'}</a>
        <div class="card-sub">
          <span>${displayYear} • ${displayGenre}</span>
          <span class="stars-gold">${stars}</span>
        </div>
        ${whyText ? `<div class="why-box">${whyText}</div>` : ''}
      </div>
    </div>
  `;
}

// Watchlist API Toggle
async function toggleWatchlist(movieId, event) {
  if (event) event.preventDefault();
  
  const card = document.querySelector(`.movie-card[data-id="${movieId}"]`);
  const btn = event ? event.currentTarget : (card ? card.querySelector('.watchlist-btn') : null);
  
  const isActive = btn && btn.classList.contains('active');
  const endpoint = isActive ? '/api/watchlist/remove' : '/api/watchlist/add';
  
  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ movie_id: movieId })
    });
    const data = await res.json();
    if (data.success) {
      if (btn) {
        btn.classList.toggle('active', !isActive);
        btn.innerHTML = !isActive ? '✓' : '+';
      }
      showToast(!isActive ? 'Added to Watchlist!' : 'Removed from Watchlist');
      if (window.location.pathname === '/watchlist') {
        loadWatchlistGrid();
      }
    } else if (data.error && res.status === 401) {
      openAuthModal();
    }
  } catch (err) {
    console.error('Error toggling watchlist:', err);
  }
}

// Interactive Star Rating Widget Helper
function updateRatingWidgetUi(rating) {
  const starsContainer = document.getElementById('interactive-stars');
  if (!starsContainer) return;
  const stars = starsContainer.querySelectorAll('.rating-star-btn');
  stars.forEach((star, idx) => {
    if (idx < rating) {
      star.classList.add('selected');
    } else {
      star.classList.remove('selected');
    }
  });
  const label = document.getElementById('user-rating-label');
  if (label) label.innerText = `Your Rating: ${rating} / 5 Stars`;
}

// Toast Notification
function showToast(msg) {
  let toast = document.getElementById('cq-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'cq-toast';
    toast.style.cssText = `
      position: fixed; bottom: 24px; right: 24px; z-index: 1000;
      background: rgba(229, 9, 20, 0.95); color: #fff; padding: 12px 24px;
      border-radius: 8px; font-weight: 600; font-size: 14px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.5); transition: opacity 0.3s ease;
    `;
    document.body.appendChild(toast);
  }
  toast.innerText = msg;
  toast.style.opacity = '1';
  setTimeout(() => { toast.style.opacity = '0'; }, 2800);
}

// Video Trailer Modal
function openTrailerModal(youtubeId, title) {
  let modal = document.getElementById('trailer-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'trailer-modal';
    modal.className = 'modal-backdrop';
    modal.innerHTML = `
      <div class="modal-content">
        <button class="modal-close" onclick="closeTrailerModal()">✕</button>
        <h3 id="modal-title" style="font-size: 24px; margin-bottom: 8px;">Trailer</h3>
        <div class="video-container">
          <iframe id="trailer-iframe" src="" allowfullscreen allow="autoplay"></iframe>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
  }
  document.getElementById('modal-title').innerText = `${title} — Official Trailer`;
  document.getElementById('trailer-iframe').src = `https://www.youtube.com/embed/${youtubeId}?autoplay=1`;
  modal.classList.add('active');
}

function closeTrailerModal() {
  const modal = document.getElementById('trailer-modal');
  if (modal) {
    modal.classList.remove('active');
    document.getElementById('trailer-iframe').src = '';
  }
}

// ---------------------------------------------------------------------------
// Navbar User Dropdown & Auth Controls
// ---------------------------------------------------------------------------

function toggleUserDropdown(e) {
  if (e) e.stopPropagation();
  const dropdown = document.getElementById('user-dropdown');
  if (dropdown) dropdown.classList.toggle('show');
}

document.addEventListener('click', () => {
  const dropdown = document.getElementById('user-dropdown');
  if (dropdown) dropdown.classList.remove('show');
});

function openAuthModal() {
  const modal = document.getElementById('auth-modal');
  if (modal) modal.classList.add('active');
}

function closeAuthModal() {
  const modal = document.getElementById('auth-modal');
  if (modal) modal.classList.remove('active');
}

function switchAuthTab(tabName) {
  document.querySelectorAll('.auth-tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.auth-tab-panel').forEach(panel => panel.classList.remove('active'));
  
  const btn = document.getElementById(`tab-btn-${tabName}`);
  const panel = document.getElementById(`auth-tab-${tabName}`);
  if (btn) btn.classList.add('active');
  if (panel) panel.classList.add('active');
}

// Google Sign-In Triggers
function triggerGoogleSignIn() {
  window.location.assign('/auth/google');
}

let pendingRegistrationEmail = '';

// Email Sign In & Registration Handlers
async function handleEmailLogin(e) {
  e.preventDefault();
  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, password: password })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Welcome back, ${data.user.name}!`);
      closeAuthModal();
      window.location.reload();
    } else {
      showToast(data.error || 'Invalid credentials');
    }
  } catch (err) {
    console.error('Login error:', err);
    showToast('Could not sign in. Please try again.');
  }
}

async function handleEmailRegister(e) {
  e.preventDefault();
  const name = document.getElementById('reg-name').value.trim();
  const email = document.getElementById('reg-email').value.trim();
  const password = document.getElementById('reg-password').value;
  const confirmPassword = document.getElementById('reg-confirm-password').value;

  if (password !== confirmPassword) {
    showToast('Passwords do not match');
    return;
  }

  try {
    const res = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name,
        email: email,
        password: password,
        confirm_password: confirmPassword
      })
    });
    const data = await res.json();
    if (data.success) {
      pendingRegistrationEmail = email;
      document.getElementById('registration-email-display').textContent = email;
      document.querySelector('#auth-tab-register > form').hidden = true;
      document.getElementById('registration-otp-step').hidden = false;
      showToast('Verification code sent to your email');
    } else {
      showToast(data.error || 'Registration failed');
    }
  } catch (err) {
    console.error('Registration error:', err);
    showToast('Could not start registration. Please try again.');
  }
}

async function verifyRegistrationOtp(e) {
  e.preventDefault();
  const otp = document.getElementById('reg-otp').value.trim();
  try {
    const res = await fetch('/api/auth/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: pendingRegistrationEmail, otp: otp })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Email verified. Your account is ready.');
      closeAuthModal();
      window.location.reload();
    } else {
      showToast(data.error || 'Email verification failed');
    }
  } catch (err) {
    console.error('Email verification error:', err);
    showToast('Could not verify your email. Please try again.');
  }
}

async function resendRegistrationOtp() {
  try {
    const res = await fetch('/api/auth/resend-otp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: pendingRegistrationEmail })
    });
    const data = await res.json();
    showToast(data.success ? 'A new verification code was sent' : (data.error || 'Could not resend code'));
  } catch (err) {
    console.error('OTP resend error:', err);
    showToast('Could not resend the verification code. Please try again.');
  }
}

async function handleAdminLogin(e) {
  e.preventDefault();
  const email = document.getElementById('admin-login-email').value.trim();
  const password = document.getElementById('admin-login-password').value;
  try {
    const res = await fetch('/api/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, password: password })
    });
    const data = await res.json();
    if (data.success) {
      closeAuthModal();
      window.location.reload();
    } else {
      showToast(data.error || 'Administrator sign-in failed');
    }
  } catch (err) {
    console.error('Administrator login error:', err);
    showToast('Could not sign in. Please try again.');
  }
}

async function logoutUser() {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
    showToast('Signed out');
    window.location.reload();
  } catch (err) {
    console.error('Logout error:', err);
  }
}

const authQuery = new URLSearchParams(window.location.search);
if (authQuery.get('google_signup') === '1') {
  openAuthModal();
  switchAuthTab('register');
}
if (authQuery.has('auth_error')) {
  const authError = authQuery.get('auth_error');
  const message = authError === 'google_linked'
    ? 'That Google account is already linked to another CQ account.'
    : (authError === 'google_admin'
      ? 'Administrator accounts must use administrator sign-in.'
      : 'Google sign-in could not be completed. Please try again.');
  showToast(message);
  window.history.replaceState({}, document.title, window.location.pathname);
} else if (authQuery.has('google_signup')) {
  window.history.replaceState({}, document.title, window.location.pathname);
}

// ---------------------------------------------------------------------------
// Admin Portal Dashboard Logic
// ---------------------------------------------------------------------------

let adminUsersData = [];
let adminRatingsData = [];
let adminMoviesData = [];

async function initAdminDashboard() {
  await Promise.all([
    loadAdminUsers(),
    loadAdminRatings(),
    loadAdminMovies()
  ]);
}

function switchAdminTab(tabName) {
  document.querySelectorAll('.admin-tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.admin-panel').forEach(panel => panel.classList.remove('active'));

  const btn = document.getElementById(`btn-tab-${tabName}`);
  const panel = document.getElementById(`admin-tab-${tabName}`);
  if (btn) btn.classList.add('active');
  if (panel) panel.classList.add('active');
}

function escapeAdminHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
}

// Admin Tab 1: Users
async function loadAdminUsers() {
  try {
    const res = await fetch('/api/admin/users');
    const data = await res.json();
    if (data.users) {
      adminUsersData = data.users;
      document.getElementById('stat-total-users').innerText = adminUsersData.length;
      renderAdminUsersTable(adminUsersData);
    }
  } catch (err) {
    console.error('Error loading admin users:', err);
  }
}

function renderAdminUsersTable(users) {
  const tbody = document.getElementById('users-tbody');
  if (!tbody) return;

  if (users.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; color:var(--text-muted);">No users found.</td></tr>`;
    return;
  }

  tbody.innerHTML = users.map(u => `
    <tr>
      <td><code>#${u.user_id}</code></td>
      <td>
        <div style="display:flex; align-items:center; gap:10px;">
          <img src="${escapeAdminHtml(u.avatar)}" alt="" style="width:30px; height:30px; border-radius:50%; object-fit:cover;" />
          <strong style="color:#fff;">${escapeAdminHtml(u.name)}</strong>
        </div>
      </td>
      <td>${escapeAdminHtml(u.email)}</td>
      <td><span class="auth-provider-badge">${escapeAdminHtml((u.auth_methods || [u.auth_provider]).join(' + '))}</span></td>
      <td><span class="role-badge">${escapeAdminHtml(u.role)}</span></td>
      <td><strong>${Number(u.ratings_count) || 0}</strong> ratings</td>
      <td><strong>${Number(u.watchlist_count) || 0}</strong> items</td>
      <td><small style="color:var(--text-muted);">${escapeAdminHtml(u.created_at || 'N/A')}</small></td>
      <td>
        <button class="btn btn-secondary btn-sm" data-user-edit="${u.user_id}">Edit</button>
        <button class="btn btn-danger btn-sm" data-user-delete="${u.user_id}">Delete</button>
      </td>
    </tr>
  `).join('');

  tbody.querySelectorAll('[data-user-edit]').forEach(button => {
    button.addEventListener('click', () => openAdminUserModal(Number(button.dataset.userEdit)));
  });
  tbody.querySelectorAll('[data-user-delete]').forEach(button => {
    button.addEventListener('click', () => {
      const user = adminUsersData.find(item => item.user_id === Number(button.dataset.userDelete));
      if (user) deleteAdminUser(user.user_id, user.name);
    });
  });
}

function filterAdminUsersTable() {
  const q = document.getElementById('user-search-input').value.toLowerCase();
  const filtered = adminUsersData.filter(u =>
    String(u.user_id).includes(q) ||
    (u.name || '').toLowerCase().includes(q) ||
    (u.email || '').toLowerCase().includes(q)
  );
  renderAdminUsersTable(filtered);
}

async function openAdminUserModal(userId) {
  const response = await fetch(`/api/admin/users/${userId}`);
  const data = await response.json();
  if (!response.ok) {
    showToast(data.error || 'Could not load user.');
    return;
  }
  const user = data.user;
  document.getElementById('edit-user-id').value = user.user_id;
  document.getElementById('edit-user-name').value = user.name || '';
  document.getElementById('edit-user-genres').value = (user.favorite_genres || []).join(', ');
  document.getElementById('edit-user-modal').classList.add('active');
}

function closeAdminUserModal() {
  document.getElementById('edit-user-modal').classList.remove('active');
}

async function submitAdminUserEdit(event) {
  event.preventDefault();
  const userId = document.getElementById('edit-user-id').value;
  const response = await fetch(`/api/admin/users/${userId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: document.getElementById('edit-user-name').value.trim(),
      favorite_genres: document.getElementById('edit-user-genres').value.split(',').map(value => value.trim()).filter(Boolean)
    })
  });
  const data = await response.json();
  if (!response.ok) {
    showToast(data.error || 'Could not save user profile.');
    return;
  }
  showToast('User profile saved.');
  closeAdminUserModal();
  await loadAdminUsers();
}

async function deleteAdminUser(userId, name) {
  if (!confirm(`Are you sure you want to permanently delete user "${name}" and all their ratings & watchlist?`)) return;

  try {
    const res = await fetch(`/api/admin/users/${userId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      showToast(`User "${name}" deleted.`);
      loadAdminUsers();
      loadAdminRatings();
    } else {
      showToast(data.error || 'Failed to delete user');
    }
  } catch (err) {
    console.error('Delete user error:', err);
  }
}

// Admin Tab 2: Ratings & Reviews
async function loadAdminRatings() {
  try {
    const res = await fetch('/api/admin/ratings');
    const data = await res.json();
    if (data.ratings) {
      adminRatingsData = data.ratings;
      document.getElementById('stat-total-ratings').innerText = adminRatingsData.length;
      renderAdminRatingsTable(adminRatingsData);
    }
  } catch (err) {
    console.error('Error loading admin ratings:', err);
  }
}

function renderAdminRatingsTable(ratings) {
  const tbody = document.getElementById('ratings-tbody');
  if (!tbody) return;

  if (ratings.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--text-muted);">No user ratings or reviews recorded.</td></tr>`;
    return;
  }

  tbody.innerHTML = ratings.map(r => `
    <tr>
      <td>
        <div>
          <strong style="color:#fff;">${escapeAdminHtml(r.user_name)}</strong>
          <div style="font-size:11px; color:var(--text-muted);">${escapeAdminHtml(r.user_email)}</div>
        </div>
      </td>
      <td>
        <div style="display:flex; align-items:center; gap:8px;">
          <img src="${escapeAdminHtml(r.movie_poster)}" alt="" style="width:28px; height:40px; object-fit:cover; border-radius:4px;" />
          <strong>${escapeAdminHtml(r.movie_title)}</strong>
        </div>
      </td>
      <td><span style="color:var(--imdb-yellow); font-weight:800;">${r.rating} ★</span></td>
      <td>
        ${r.review ? `<span style="color:var(--text-primary);">"${escapeAdminHtml(r.review)}"</span>` : `<em style="color:var(--text-muted); font-size:12px;">No written review</em>`}
      </td>
      <td><small style="color:var(--text-muted);">${escapeAdminHtml(r.updated_at)}</small></td>
      <td>
        <div style="display:flex; gap:6px;">
          <button class="btn btn-secondary btn-sm" data-rating-edit="${r.user_id}" data-movie-id="${r.movie_id}">Edit</button>
          <button class="btn btn-danger btn-sm" data-rating-delete="${r.user_id}" data-movie-id="${r.movie_id}">Delete</button>
        </div>
      </td>
    </tr>
  `).join('');

  tbody.querySelectorAll('[data-rating-edit]').forEach(button => {
    button.addEventListener('click', () => {
      const rating = adminRatingsData.find(item =>
        item.user_id === Number(button.dataset.ratingEdit) && item.movie_id === Number(button.dataset.movieId)
      );
      if (rating) openEditRatingModal(rating.user_id, rating.movie_id, rating.user_name, rating.movie_title, rating.rating, rating.review || '');
    });
  });
  tbody.querySelectorAll('[data-rating-delete]').forEach(button => {
    button.addEventListener('click', () => deleteAdminRating(
      Number(button.dataset.ratingDelete), Number(button.dataset.movieId)
    ));
  });
}

function filterAdminRatingsTable() {
  const q = document.getElementById('rating-search-input').value.toLowerCase();
  const filtered = adminRatingsData.filter(r => r.user_name.toLowerCase().includes(q) || r.movie_title.toLowerCase().includes(q) || (r.review && r.review.toLowerCase().includes(q)));
  renderAdminRatingsTable(filtered);
}

function openEditRatingModal(userId, movieId, userName, movieTitle, ratingVal, reviewText) {
  document.getElementById('edit-rating-userid').value = userId;
  document.getElementById('edit-rating-movieid').value = movieId;
  document.getElementById('edit-rating-username').value = userName;
  document.getElementById('edit-rating-movietitle').value = movieTitle;
  document.getElementById('edit-rating-val').value = ratingVal;
  document.getElementById('edit-rating-review').value = reviewText;

  const modal = document.getElementById('edit-rating-modal');
  if (modal) modal.classList.add('active');
}

function closeEditRatingModal() {
  const modal = document.getElementById('edit-rating-modal');
  if (modal) modal.classList.remove('active');
}

async function submitAdminEditRating(e) {
  e.preventDefault();
  const userId = document.getElementById('edit-rating-userid').value;
  const movieId = document.getElementById('edit-rating-movieid').value;
  const rating = document.getElementById('edit-rating-val').value;
  const review = document.getElementById('edit-rating-review').value;

  try {
    const res = await fetch('/api/admin/ratings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, movie_id: movieId, rating: rating, review: review })
    });
    const data = await res.json();
    if (data.success) {
      showToast('User rating updated!');
      closeEditRatingModal();
      loadAdminRatings();
    }
  } catch (err) {
    console.error('Error editing rating:', err);
  }
}

async function deleteAdminRating(userId, movieId) {
  if (!confirm('Are you sure you want to delete this rating?')) return;
  try {
    const res = await fetch('/api/admin/ratings', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, movie_id: movieId })
    });
    const data = await res.json();
    if (data.success) {
      showToast('Rating deleted.');
      loadAdminRatings();
    }
  } catch (err) {
    console.error('Error deleting rating:', err);
  }
}

function deleteRatingFromModal() {
  const userId = document.getElementById('edit-rating-userid').value;
  const movieId = document.getElementById('edit-rating-movieid').value;
  closeEditRatingModal();
  deleteAdminRating(userId, movieId);
}

// Admin Tab 3: Movies Catalog
async function loadAdminMovies() {
  try {
    const res = await fetch('/api/admin/movies');
    const data = await res.json();
    if (data.movies) {
      adminMoviesData = data.movies;
      document.getElementById('stat-total-movies').innerText = adminMoviesData.length;
      renderAdminMoviesTable(adminMoviesData);
    }
  } catch (err) {
    console.error('Error loading admin movies:', err);
  }
}

function renderAdminMoviesTable(movies) {
  const tbody = document.getElementById('movies-tbody');
  if (!tbody) return;

  if (!movies.length) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-muted);">No MongoDB movies found. Sync a page from TMDB.</td></tr>';
    return;
  }

  tbody.innerHTML = movies.map(m => `
    <tr>
      <td><code>#${m.tmdb_id}</code></td>
      <td><img src="${escapeAdminHtml(m.poster_url)}" alt="" style="width:32px; height:46px; object-fit:cover; border-radius:4px;" /></td>
      <td><strong style="color:#fff;">${escapeAdminHtml(m.title)}</strong></td>
      <td>${escapeAdminHtml(m.year || '')}</td>
      <td><span style="font-size:12px; color:var(--text-secondary);">${escapeAdminHtml((m.genres || []).join(', '))}</span></td>
      <td>${escapeAdminHtml(m.director || (m.directors || []).join(', '))}</td>
      <td><span style="color:var(--imdb-yellow); font-weight:700;">★ ${m.tmdb_rating == null ? 'N/A' : Number(m.tmdb_rating).toFixed(1)}</span></td>
      <td>
        <div style="display:flex; gap:6px;">
          <button class="btn btn-secondary btn-sm" data-movie-edit="${m.tmdb_id}">Edit</button>
          <button class="btn btn-danger btn-sm" data-movie-delete="${m.tmdb_id}">Delete</button>
        </div>
      </td>
    </tr>
  `).join('');

  tbody.querySelectorAll('[data-movie-edit]').forEach(button => {
    button.addEventListener('click', () => openEditMovieModal(Number(button.dataset.movieEdit)));
  });
  tbody.querySelectorAll('[data-movie-delete]').forEach(button => {
    button.addEventListener('click', () => {
      const movie = adminMoviesData.find(item => item.tmdb_id === Number(button.dataset.movieDelete));
      if (movie) deleteAdminMovie(movie.tmdb_id, movie.title);
    });
  });
}

function filterAdminMoviesTable() {
  const q = document.getElementById('movie-search-input').value.toLowerCase();
  const filtered = adminMoviesData.filter(m =>
    String(m.tmdb_id).includes(q) ||
    (m.title || '').toLowerCase().includes(q) ||
    (m.director || '').toLowerCase().includes(q) ||
    (m.genres || []).some(genre => genre.toLowerCase().includes(q))
  );
  renderAdminMoviesTable(filtered);
}

async function syncTmdbMovies() {
  const query = document.getElementById('tmdb-sync-query').value.trim();
  const response = await fetch('/api/admin/movies/sync', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: query, page: 1, region: 'IN' })
  });
  const data = await response.json();
  if (!response.ok) {
    showToast(data.error || 'TMDB sync failed.');
    return;
  }
  showToast(`Synced ${data.synced} TMDB movies.`);
  await loadAdminMovies();
}

function openEditMovieModal(tmdbId) {
  const movie = adminMoviesData.find(item => item.tmdb_id === tmdbId);
  if (!movie) return;

  document.getElementById('movie-modal-title').textContent = `Edit ${movie.title}`;
  document.getElementById('edit-movie-id').value = movie.tmdb_id;
  document.getElementById('movie-title').value = movie.title || '';
  document.getElementById('movie-tagline').value = movie.tagline || '';
  document.getElementById('movie-genres').value = (movie.genres || []).join(', ');
  document.getElementById('movie-overview').value = movie.overview || '';
  document.getElementById('edit-movie-modal').classList.add('active');
}

function closeEditMovieModal() {
  const modal = document.getElementById('edit-movie-modal');
  if (modal) modal.classList.remove('active');
}

async function submitAdminSaveMovie(e) {
  e.preventDefault();
  const tmdbId = document.getElementById('edit-movie-id').value;

  const moviePayload = {
    title: document.getElementById('movie-title').value.trim(),
    genres: document.getElementById('movie-genres').value.split(',').map(s => s.trim()).filter(Boolean),
    tagline: document.getElementById('movie-tagline').value.trim(),
    overview: document.getElementById('movie-overview').value.trim()
  };

  try {
    const res = await fetch(`/api/admin/movies/${tmdbId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(moviePayload)
    });
    const data = await res.json();
    if (data.success) {
      showToast('Movie changes saved.');
      closeEditMovieModal();
      await loadAdminMovies();
    } else {
      showToast(data.error || 'Could not save movie.');
    }
  } catch (err) {
    console.error('Error saving movie:', err);
  }
}

async function deleteAdminMovie(tmdbId, title) {
  if (!confirm(`Remove "${title}" from the active catalog? Existing user ratings and watchlist references will be preserved.`)) return;

  try {
    const res = await fetch(`/api/admin/movies/${tmdbId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      showToast(`Movie "${title}" removed from the catalog.`);
      await loadAdminMovies();
      await loadAdminRatings();
    } else {
      showToast(data.error || 'Could not remove movie.');
    }
  } catch (err) {
    console.error('Delete movie error:', err);
  }
}
