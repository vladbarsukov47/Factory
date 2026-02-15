(function () {
  const qtyInput = document.querySelector('input[name="qty"]');
  if (!qtyInput) {
    return;
  }

  qtyInput.addEventListener('input', () => {
    qtyInput.value = qtyInput.value.replace(',', '.');
  });
})();